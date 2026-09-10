"""Running one correction, and keeping everything it did — including the failures.

Q0, a pinned transaction, the operations the model asked for, Q1, the evaluator,
then commit or verified rollback. The driver executes; it does not choose. A
correction arrives as data — an objective, a policy, a locality declaration and a
list of typed operation requests — which is what makes any reasoning client able
to produce one without RoboVision knowing which.

Rejected trajectories are kept as carefully as accepted ones. The interesting
question after a run is not "did it improve" but "which kinds of correction
succeed, which fail repeatedly, and which operation sequences keep recurring",
and half that evidence is in the attempts that were rolled back.

`indeterminate` never commits. A candidate whose result could not be established
is not a candidate that passed, and the safe reading of "I cannot tell" is to put
the scene back.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artist_loop import Brief, LOOP_SCHEMA, advisory_ranking, measure, packet, resolutions
from .errors import RoboVisionError
from .session import HostSession


@dataclass
class Correction:
    """What a reasoning client emits. Deliberately plain data.

    `objective` is the model's own words and is recorded rather than parsed: it
    is evidence about the model's reasoning, not an instruction to the driver.
    """

    objective: str
    reason: str
    targets: list[dict[str, Any]]
    protected: list[dict[str, Any]] = field(default_factory=list)
    locality_targets: list[str] = field(default_factory=list)
    locality_protected: list[str] = field(default_factory=list)
    locality_allowed: list[str] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    advisory_metrics: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "objective": self.objective, "reason": self.reason,
            "targets": self.targets, "protected": self.protected,
            "locality": {"targets": self.locality_targets,
                         "protected": self.locality_protected,
                         "allowed": self.locality_allowed},
            "operations": self.operations,
        }


class Trajectory:
    """Every attempt, in order, with the evidence each one was judged on."""

    def __init__(self, root: Path, brief: Brief, model: str) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.brief = brief
        self.model = model
        self.attempts: list[dict[str, Any]] = []
        self.started = time.time()
        self.manual_interventions: list[str] = []
        self.stop_reason: str | None = None

    def note_manual(self, what: str) -> None:
        """Anything a human or the harness did that the model did not.

        Counted honestly and separately, because an autonomy number that quietly
        excluded the times someone reached in would be the easiest figure in the
        whole benchmark to fake.
        """
        self.manual_interventions.append(what)

    def record(self, attempt: dict[str, Any]) -> None:
        self.attempts.append(attempt)
        self.write()

    def summary(self) -> dict[str, Any]:
        decisions = [a["evaluation"]["decision"] for a in self.attempts]
        return {
            "attempts": len(self.attempts),
            "accepted": decisions.count("accept"),
            "rejected": decisions.count("reject"),
            "indeterminate": decisions.count("indeterminate"),
            "rollbacks": sum(1 for a in self.attempts if a["outcome"] == "rolled_back"),
            "tool_calls": sum(a["tool_calls"] for a in self.attempts),
            "elapsed_seconds": round(time.time() - self.started, 3),
            "manual_interventions": len(self.manual_interventions),
            "stop_reason": self.stop_reason,
        }

    def write(self) -> Path:
        path = self.root / "trajectory.json"
        path.write_text(json.dumps({
            "schema": LOOP_SCHEMA,
            "model": self.model,
            "brief": self.brief.to_json(),
            "summary": self.summary(),
            "manual_interventions": self.manual_interventions,
            "attempts": self.attempts,
        }, indent=2, default=str), encoding="utf-8")
        return path


def _result(response: dict[str, Any]) -> dict[str, Any]:
    return response.get("result") or {}


def _policy(correction: Correction, locality: dict[str, Any] | None,
            brief: Brief) -> dict[str, Any]:
    return {
        "targets": correction.targets,
        "protected": correction.protected,
        "required_invariants": brief.required_invariants,
        "advisory": correction.advisory_metrics,
        "locality": locality,
        "require_locality": locality is not None,
    }


def _epsilon_audit(correction: Correction, before: dict[str, Any],
                   evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    """Whether each epsilon was above what the metric can actually resolve.

    A DAT fixture already caught an "improvement" that was sub-pixel and
    therefore exactly zero. An epsilon smaller than one unit of the metric's own
    quantisation cannot distinguish a real change from noise, so an improvement
    that clears it is recorded as unproven rather than as success.
    """
    known = resolutions(before)
    audit = []
    achieved = {(entry["metric"], entry.get("kind")): entry
                for entry in evaluation["targets_achieved"]}
    for target in correction.targets:
        key = f"{target.get('kind')}/{target['metric']}"
        resolution = known.get(key)
        entry = achieved.get((target["metric"], target.get("kind")))
        audit.append({
            "metric": target["metric"], "kind": target.get("kind"),
            "epsilon": target["epsilon"],
            "metric_resolution": resolution,
            "epsilon_above_resolution": None if resolution is None
            else target["epsilon"] >= resolution,
            "achieved": entry["achieved_improvement"] if entry else None,
            "improvement_above_resolution": None if (resolution is None or entry is None)
            else entry["achieved_improvement"] >= resolution,
        })
    return audit


def run_correction(session: HostSession, brief: Brief, correction: Correction,
                   trajectory: Trajectory) -> dict[str, Any]:
    """One attempt, start to finish, whatever the outcome."""
    started = time.time()
    calls = 0
    attempt_id = "attempt:" + uuid.uuid4().hex[:12]

    health = _result(session.health())
    calls += 1
    if health["ready_for"]["begin_correction"]["status"] != "ready":
        raise RoboVisionError(
            "CORRECTION_UNAVAILABLE",
            "the host cannot presently carry out a transactional correction",
            data=health["ready_for"]["begin_correction"])

    # The envelope, not just its result: `revision` rides on every response and is
    # the pin a transaction is opened against.
    snapshot_response = session.call("scene.snapshot", {"level": "deep"})
    snapshot = _result(snapshot_response)
    revision = int(snapshot_response["revision"])
    calls += 1
    before = measure(session, brief)
    calls += len(before)
    evidence = packet(before, brief)

    world = health["identity"]["world_incarnation"]
    contract = health["identity"]["coordinate_contract"]
    begun = session.begin_transaction(
        correction.objective[:60], if_revision=revision,
        expected_world=world, expected_coordinate_contract=contract,
        contract="autonomous")
    calls += 1
    transaction = _result(begun)["transaction"]

    operations: list[dict[str, Any]] = []
    failure: dict[str, Any] | None = None
    for request in correction.operations:
        try:
            response = session.call(request["method"], request.get("params", {}))
            calls += 1
            operations.append({"method": request["method"],
                               "params": request.get("params", {}),
                               "ok": response.get("ok"),
                               "outcome": response.get("outcome"),
                               "request_id": response.get("id"),
                               "revision": response.get("revision")})
        except RoboVisionError as exc:
            calls += 1
            failure = {"method": request["method"], "code": exc.payload.code,
                       "message": str(exc), "data": exc.payload.data}
            operations.append({"method": request["method"],
                               "params": request.get("params", {}),
                               "ok": False, "error": failure})
            break

    after = measure(session, brief)
    calls += len(after)
    locality = None
    if correction.locality_targets:
        locality = _result(session.call("truth.locality", {
            "before": snapshot["snapshot"],
            "targets": correction.locality_targets,
            "protecteds": correction.locality_protected + brief.protected_objects,
            "alloweds": correction.locality_allowed}))
        calls += 1

    policy = _policy(correction, locality, brief)
    evaluation = _result(session.call("truth.evaluate",
                                      {**policy, "before": before, "after": after}))
    calls += 1

    # An operation that failed outright is not a candidate to be judged on its
    # metrics; it is a correction that did not happen, and the scene has to go
    # back regardless of what the numbers say.
    accepted = evaluation["accepted"] and failure is None
    if accepted:
        finish = _result(session.end_transaction("transaction.commit", transaction))
        outcome, proof = "committed", finish
    else:
        finish = _result(session.end_transaction("transaction.rollback", transaction))
        outcome, proof = "rolled_back", finish
    calls += 1

    final = _result(session.call("scene.snapshot", {"level": "deep"}))
    calls += 1

    record = {
        "attempt": attempt_id,
        "model": trajectory.model,
        "started_at": round(started, 3),
        "elapsed_seconds": round(time.time() - started, 3),
        "tool_calls": calls,
        "pins": {"world": world, "coordinate_contract": contract,
                 "if_revision": revision},
        "discrepancy_packet": evidence,
        "advisory_ranking": advisory_ranking(evidence),
        "correction": correction.to_json(),
        "operation_failure": failure,
        "operations": operations,
        "q0": {kind: certificate.get("certificate") for kind, certificate in before.items()},
        "q1": {kind: certificate.get("certificate") for kind, certificate in after.items()},
        "q0_full": before,
        "q1_full": after,
        "locality": locality,
        "policy": {k: v for k, v in policy.items() if k != "locality"},
        "evaluation": {k: v for k, v in evaluation.items() if k != "certificates"},
        "epsilon_audit": _epsilon_audit(correction, before, evaluation),
        "outcome": outcome,
        "transaction": transaction,
        "finish_proof": proof,
        "begin_fingerprint": snapshot["fingerprint"],
        "final_fingerprint": final["fingerprint"],
        "restored": final["fingerprint"] == snapshot["fingerprint"],
    }
    trajectory.record(record)
    return record


def capture_views(session: HostSession, brief: Brief, root: Path, label: str,
                  axes=("FRONT", "RIGHT", "TOP")) -> list[dict[str, Any]]:
    """Shaded axis captures for a human to look at. Not a measurement.

    Deliberately separate from every DAT number in this file. These are Blender
    viewport renders framed on the subject — they carry shading and materials and
    are therefore the only evidence here that can answer "did it get visibly
    better". They are not the canonical measurement cameras, and the record says
    so rather than letting a reader assume the picture and the metric share a
    viewpoint.
    """
    root.mkdir(parents=True, exist_ok=True)
    captured = []
    session.call("viewport.focus", {"object": brief.subjects[0], "padding": 2.2})
    for axis in axes:
        session.call("viewport.axis", {"axis": axis})
        path = root / f"{label}-{axis.lower()}.png"
        response = _result(session.call("viewport.capture", {"path": str(path)}))
        captured.append({
            "axis": axis, "path": str(path),
            "provenance": "blender viewport axis capture, shaded; framed on the "
                          "subject and NOT the canonical measurement camera",
            "view": response.get("view"),
        })
    return captured
