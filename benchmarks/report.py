"""Flattening a run into comparable numbers, and writing the run record.

Separate from the runner because it is the part a reader of results depends on,
and because one rule matters more than the mechanics: no scalar quality score is
produced. A single number would let one metric's improvement pay for another's
regression, which is exactly the trade vector acceptance exists to refuse.

The call counts are kept apart for the same kind of reason. Reporting one
`tool_calls` figure that quietly excluded observation would describe a model as
more efficient than it was, and observation is the larger half of what a
participant spends.
"""
from __future__ import annotations

import json

from robovision.artist_loop_run import capture_views


def vector(evidence: dict) -> dict:
    """The comparable numbers, flattened. Deliberately never summed.

    No scalar quality score is produced anywhere. A single number would let one
    metric's improvement pay for another's regression, which is the exact trade
    vector acceptance exists to refuse.
    """
    flat = {
        "hard_invariants": evidence["hard_invariants"]["status"],
        "failing_invariants": evidence["hard_invariants"]["failing"],
        "dimensions": evidence["dimensions"]["size"],
        "coverage.observed_fraction": evidence["coverage"]["observed_fraction"],
    }
    for key, entry in (evidence.get("reference") or {}).items():
        for name, value in entry.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                flat[f"reference[{key}].{name}"] = value
    for key, entry in (evidence.get("patterns") or {}).items():
        flat[f"pattern[{key}].failing"] = entry.get("failing", [])
        for name, value in entry.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                flat[f"pattern[{key}].{name}"] = value
    return flat


def finalize(runner: "Runner", tokens: str | None) -> dict:
    trajectory = runner.trajectory()
    final = runner.observe(label="packet-final")
    q0_path = runner.root / "packet-q0.json"
    q0 = json.loads(q0_path.read_text(encoding="utf-8")) if q0_path.is_file() else None
    captures = capture_views(runner.session, trajectory.brief,
                             runner.root / "captures", "final")
    decisions = [a["evaluation"]["decision"] for a in trajectory.attempts]
    execution = sum(a.get("tool_calls", 0) for a in trajectory.attempts)
    observation = runner.calls()["participant_observation_tool_calls"]
    stop = runner.root / "stop.json"
    verification = runner.root / "restore-verification.json"

    report = {
        "benchmark": runner.manifest["benchmark"],
        "model": runner.model,
        "restore_verification": json.loads(verification.read_text(encoding="utf-8"))
        if verification.is_file() else None,
        # Not observable from inside the harness. Recorded as self-reported or
        # left null rather than estimated.
        "token_usage": {"value": tokens, "source": "self-reported by the operator"}
        if tokens else {"value": None, "source": "not observable from the harness"},
        "attempts": {
            "total": len(trajectory.attempts),
            "budget": runner.manifest["budget"]["max_attempts"],
            "accepted": decisions.count("accept"),
            "rejected": decisions.count("reject"),
            "indeterminate": decisions.count("indeterminate"),
            "invalid_or_incomplete": [
                {"attempt": a["attempt"], "validity": a.get("benchmark_validity"),
                 "phase": a.get("phase"), "outcome": a["outcome"]}
                for a in trajectory.attempts
                if a.get("benchmark_validity") not in (None, "valid")],
        },
        "rollback_proofs": [
            {"attempt": a["attempt"], "outcome": a["outcome"],
             "begin_fingerprint": a.get("begin_fingerprint"),
             "final_fingerprint": a.get("final_fingerprint"),
             "restored": a.get("restored")}
            for a in trajectory.attempts],
        # Named, not merged. `correction_execution_tool_calls` is what the
        # attempts cost; observation is what looking cost. One number covering
        # both would be a claim about efficiency that neither supports alone.
        "correction_execution_tool_calls": execution,
        "participant_observation_tool_calls": observation,
        "total_participant_facing_calls": execution + observation,
        "observation_budgeted": bool(
            runner.manifest["budget"].get("observation_budgeted")),
        "elapsed_seconds": round(sum(a.get("elapsed_seconds", 0.0)
                                     for a in trajectory.attempts), 3),
        "manual_interventions": trajectory.manual_interventions,
        "stop": json.loads(stop.read_text(encoding="utf-8")) if stop.is_file() else None,
        "q0_vector": vector(q0) if q0 else None,
        "final_vector": vector(final),
        "captures": captures,
        "known_limits": runner.manifest["known_limits"],
        "note": "no scalar quality score is produced; the vectors are compared "
                "component by component",
    }
    (runner.root / "report.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report
