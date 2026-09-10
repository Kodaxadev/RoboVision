"""The measurement verbs an Artist Loop compares Q0 and Q1 with.

Reads, all of them, and authoritative ones: a measurement stamped with the
revision of the state before it would be worse than no measurement, because the
whole mechanic is putting two of them beside each other and believing the
difference.

`truth.measure` is the one a loop calls. The individual kinds are published too,
because an agent narrowing down a problem should not have to pay for a
self-intersection search to re-read a bounding box.
"""
from __future__ import annotations

from typing import Any

from ..registry import AUTHORITATIVE, INDEPENDENT, HostError
from ..truth import geometry, spatial
from ..truth.certificate import comparable

KINDS = ("geometry", "spatial")


def _epsilon(params: dict[str, Any]) -> float:
    try:
        value = float(params.get("epsilon", 1e-10))
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", "epsilon must be a number") from exc
    if value <= 0.0:
        raise HostError("INVALID_PARAMS", "epsilon must be positive")
    return value


def _max_dimension(params: dict[str, Any]) -> float | None:
    value = params.get("max_dimension")
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", "max_dimension must be a number") from exc
    if value <= 0.0:
        raise HostError("INVALID_PARAMS", "max_dimension must be positive")
    return value


def geometry_truth(params, runtime):
    objects, intentional_open = geometry.resolve_subjects(params)
    return geometry.measure(
        objects,
        intentional_open=intentional_open,
        epsilon=_epsilon(params),
        # The overlap search is the only part of this that is not cheap, so it is
        # opt-out rather than always paid for. It defaults on: a loop that
        # forgot to ask should get the stricter answer, not the faster one.
        check_intersections=bool(params.get("check_intersections", True)),
        runtime=runtime,
    ).certificate()


def spatial_truth(params, runtime):
    objects, _ = geometry.resolve_subjects(params)
    return spatial.measure(objects, max_dimension=_max_dimension(params),
                           runtime=runtime).certificate()


def measure(params, runtime):
    """Every requested kind, taken from one authoritative read of one moment.

    Bundled rather than left to the caller to assemble, because two certificates
    fetched separately describe two moments. A human edit between them would put
    a geometry measurement and a dimension measurement on either side of a change
    neither of them mentions.
    """
    kinds = params.get("kinds", list(KINDS))
    if not isinstance(kinds, list) or not kinds:
        raise HostError("INVALID_PARAMS", "kinds must be a non-empty array")
    unknown = [kind for kind in kinds if kind not in KINDS]
    if unknown:
        raise HostError("INVALID_PARAMS", f"unknown measurement kinds: {unknown}",
                        data={"supported": list(KINDS)})

    certificates: dict[str, Any] = {}
    if "geometry" in kinds:
        certificates["geometry"] = geometry_truth(params, runtime)
    if "spatial" in kinds:
        certificates["spatial"] = spatial_truth(params, runtime)

    holds = all(entry["invariants_hold"] for entry in certificates.values())
    unmeasured = {name: reason for entry in certificates.values()
                  for name, reason in entry["limits"].items()}
    return {
        "kinds": sorted(certificates),
        "certificates": certificates,
        # The one aggregate worth having, and it is a gate rather than a score.
        # No overall quality number: a single figure is exactly what would let a
        # loop trade an invariant for an improvement.
        "invariants_hold": holds,
        "failing_invariants": sorted(
            name for entry in certificates.values()
            for name, invariant in entry["invariants"].items() if not invariant["holds"]),
        "unmeasured": unmeasured,
    }


def compare(params, runtime):
    """Two certificates, and exactly what moved between them.

    The refusal matters more than the arithmetic. A Q0 and a Q1 from different
    worlds, different coordinate contracts or different subjects are not a before
    and an after, and computing a confident delta over them would be the most
    convincing wrong answer this system could produce.
    """
    before = params.get("before")
    after = params.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise HostError("INVALID_PARAMS", "before and after must be certificates")
    for side, value in (("before", before), ("after", after)):
        if "metrics" not in value or "pins" not in value:
            raise HostError("INVALID_PARAMS", f"{side} is not a measurement certificate")

    ok, reason = comparable(before, after)
    if not ok:
        raise HostError(
            "INCOMPARABLE_MEASUREMENTS",
            "those two certificates do not describe the same thing measured twice",
            data={"reason": reason,
                  "before_pins": before["pins"], "after_pins": after["pins"]},
        )

    deltas: dict[str, Any] = {}
    for name, entry in after["metrics"].items():
        if name not in before["metrics"]:
            deltas[name] = {"delta": None, "reason": "not_measured_before"}
            continue
        was = before["metrics"][name]["value"]
        now = entry["value"]
        change = now - was
        direction = entry["direction"]
        if change == 0:
            movement = "unchanged"
        elif direction == "neutral":
            movement = "changed"
        else:
            better = change < 0 if direction == "lower_better" else change > 0
            movement = "improved" if better else "regressed"
        deltas[name] = {"before": was, "after": now, "delta": round(change, 9),
                        "direction": direction, "movement": movement}

    broke = sorted(name for name, entry in after["invariants"].items()
                   if not entry["holds"] and before["invariants"].get(name, {}).get("holds"))
    fixed = sorted(name for name, entry in after["invariants"].items()
                   if entry["holds"] and not before["invariants"].get(name, {"holds": True})["holds"])
    del runtime
    return {
        "identical": before["certificate"] == after["certificate"],
        "metrics": dict(sorted(deltas.items())),
        "improved": sorted(n for n, d in deltas.items() if d.get("movement") == "improved"),
        "regressed": sorted(n for n, d in deltas.items() if d.get("movement") == "regressed"),
        "changed": sorted(n for n, d in deltas.items() if d.get("movement") == "changed"),
        "invariants_broken": broke,
        "invariants_fixed": fixed,
        "invariants_hold": after["invariants_hold"],
    }


def register(registry) -> None:
    # Authoritative on purpose. A measurement carrying the revision of the state
    # before it is the one failure mode that would poison every comparison built
    # on it, and this whole family exists to be compared.
    registry.add("truth.measure", measure, reads=AUTHORITATIVE, stability="alpha")
    registry.add("truth.geometry", geometry_truth, reads=AUTHORITATIVE, stability="alpha")
    registry.add("truth.spatial", spatial_truth, reads=AUTHORITATIVE, stability="alpha")
    # Pure arithmetic over two certificates the caller already holds: it does not
    # look at the scene at all, and must not, or it would be measuring a third
    # moment while claiming to compare two.
    registry.add("truth.compare", compare, reads=INDEPENDENT, stability="alpha")
