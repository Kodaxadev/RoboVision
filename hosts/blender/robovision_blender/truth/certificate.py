"""What a measurement is, before anything is measured.

A number an agent cannot compare is not evidence. The Artist Loop's whole
mechanic is Q0 → candidate → Q1 → accept or reject, and that only works if two
measurements taken minutes apart can be put beside each other and disagreed
with. Three things make that possible and all three live here.

**Pins.** A measurement belongs to the state it was taken from. The world
incarnation, the coordinate contract, the authored revision and the scene
fingerprint travel with it, so a Q1 compared against a Q0 from a different world,
a reinterpreted unit or a scene somebody else moved is caught rather than
silently averaged. Comparison refuses across a broken pin; it does not warn.

**Metrics,** each a scalar with a declared direction. `lower_better` and
`higher_better` are what let a caller say "the target metric must improve by
epsilon" and "protected metrics may not regress beyond tolerance" without a
lookup table of which way is good — a table that would eventually disagree with
the measurement it describes.

**Invariants,** each a boolean with the evidence behind it. These are the hard
gate: a correction that breaks one is rejected however much it improved the
metric it was aiming at. Kept separate from metrics deliberately — "worse" and
"broken" are different outcomes and collapsing them would let a loop trade
correctness for its own objective.

Deliberately not here: retention, history, a query API, scoring, or any notion of
"good". A certificate is one measurement of one state, and the loop that consumes
it decides what to do about it.
"""
from __future__ import annotations

import hashlib
from typing import Any

from ..recipe import canonical

CERTIFICATE_SCHEMA = 1

# Which way is better, declared with the metric rather than looked up. A caller
# that had to know the direction of every metric name would eventually be wrong
# about one, and it would be wrong in the direction of accepting a regression.
LOWER_BETTER = "lower_better"
HIGHER_BETTER = "higher_better"
# A measurement that is neither good nor bad — a dimension, a count of parts.
# Still comparable, and still able to be protected against unexpected change.
NEUTRAL = "neutral"
DIRECTIONS = (LOWER_BETTER, HIGHER_BETTER, NEUTRAL)


class Measurement:
    """One certificate under construction: pins, metrics, invariants, subjects."""

    __slots__ = ("kind", "pins", "metrics", "invariants", "subjects", "notes", "limits")

    def __init__(self, kind: str, pins: dict[str, Any]):
        self.kind = kind
        self.pins = pins
        self.metrics: dict[str, dict[str, Any]] = {}
        self.invariants: dict[str, dict[str, Any]] = {}
        self.subjects: list[dict[str, Any]] = []
        self.notes: list[str] = []
        # What this measurement could not establish, and why. An unmeasured
        # property must never be indistinguishable from a passing one.
        self.limits: dict[str, str] = {}

    def metric(self, name: str, value: float | int, *, direction: str,
               unit: str = "count", **detail: Any) -> None:
        if direction not in DIRECTIONS:
            raise ValueError(f"{name}: unknown metric direction {direction!r}")
        entry: dict[str, Any] = {"value": value, "direction": direction, "unit": unit}
        if detail:
            entry["detail"] = detail
        self.metrics[name] = entry

    def invariant(self, name: str, holds: bool, **evidence: Any) -> None:
        """A hard gate, with what decided it.

        `holds` is a real boolean and never `None`: an invariant nobody could
        evaluate belongs in `limits`, because "not checked" reported as "holds"
        is exactly the failure this whole file exists to make impossible.
        """
        entry: dict[str, Any] = {"holds": bool(holds)}
        if evidence:
            entry["evidence"] = evidence
        self.invariants[name] = entry

    def unmeasured(self, name: str, reason: str) -> None:
        """Say what was not established rather than leaving it absent."""
        self.limits[name] = reason

    def certificate(self) -> dict[str, Any]:
        """The finished measurement, with an identity derived from its content.

        The id is a hash of the pins, the metric values and the invariant
        outcomes, so two measurements of genuinely identical state share one and
        any difference at all produces a different one. That makes "did anything
        change?" a string comparison before it is a diff, which is what a loop
        checking a protected region actually wants to ask first.
        """
        body = {
            "schema": CERTIFICATE_SCHEMA,
            "kind": self.kind,
            "pins": self.pins,
            "metrics": {name: entry["value"] for name, entry in sorted(self.metrics.items())},
            "invariants": {name: entry["holds"] for name, entry in sorted(self.invariants.items())},
            "limits": dict(sorted(self.limits.items())),
        }
        digest = hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()
        return {
            "certificate": "rvtruth:" + digest[:32],
            "schema": CERTIFICATE_SCHEMA,
            "kind": self.kind,
            "pins": self.pins,
            "subjects": self.subjects,
            "metrics": dict(sorted(self.metrics.items())),
            "invariants": dict(sorted(self.invariants.items())),
            "invariants_hold": all(entry["holds"] for entry in self.invariants.values()),
            "limits": dict(sorted(self.limits.items())),
            "notes": self.notes,
        }


def pins_for(runtime, subjects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The state this measurement belongs to.

    The coordinate contract is in here for the same reason a mutation pins it: a
    human can change what a unit means between Q0 and Q1 without moving the world
    incarnation or the authored revision, and a dimension measured in a different
    currency is not a comparable number.
    """
    from ..protocol import AUTHORED
    from ..recipe import coordinate_contract, units

    pins: dict[str, Any] = {
        "world_incarnation": runtime.world_incarnation,
        "coordinate_contract": coordinate_contract(),
        "units": units(),
        "state_domain": AUTHORED,
        "revision": runtime.revision,
        "fingerprint": runtime.current_snapshot()["fingerprint"],
    }
    if subjects is not None:
        # Per-subject topology revisions, because the authored revision does not
        # move for every change a mesh index depends on.
        pins["mesh_revisions"] = {
            subject["object"]: subject["mesh_revision"]
            for subject in subjects if subject.get("mesh_revision") is not None
        }
    return pins


def comparable(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str | None]:
    """Whether two certificates describe the same thing measured twice.

    Refused rather than warned about. A Q0/Q1 pair whose world, coordinate
    contract or subject set differs is not a before and an after; it is two
    unrelated measurements, and letting an epsilon comparison run over them would
    produce a confident number about nothing.

    The revision and the fingerprint are deliberately *not* required to match —
    they are exactly what a correction is supposed to change.
    """
    if before.get("kind") != after.get("kind"):
        return False, "different_measurement_kind"
    for pin in ("world_incarnation", "coordinate_contract", "state_domain"):
        if before["pins"].get(pin) != after["pins"].get(pin):
            return False, "pin_changed:" + pin
    if sorted(s["object"] for s in before["subjects"]) \
            != sorted(s["object"] for s in after["subjects"]):
        return False, "different_subjects"
    return True, None
