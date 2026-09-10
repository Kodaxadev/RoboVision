"""Run one model through the frozen correction-transfer benchmark.

Four subcommands, and the split matters: the participating model never runs this
file. It reads what `observe` prints and emits a correction JSON, and the harness
executes that JSON exactly as the Artist Loop defines it. The model's entire
influence on the run is the content of those files, which is what makes two
models comparable at all.

    restore  <model>            clean scene, replay the frozen A0, record Q0
    observe  <model>            print the discrepancy packet and health
    attempt  <model> <file>     execute one correction JSON, within budget
    finalize <model> [--tokens] final vectors, captures and the run record

The budget, the tools, the DAT policy and the references come from the package
manifest and are never taken from the command line. A benchmark whose limits can
be raised by the operator running it measures the operator.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from observe_support import load_brief  # noqa: E402
from robovision.artist_loop import advisory_ranking, measure, packet  # noqa: E402
from robovision.artist_loop_run import (  # noqa: E402
    Correction, Trajectory, capture_views, run_correction)
from robovision.session import HostSession  # noqa: E402

PACKAGE = ROOT / "benchmarks" / "correction-transfer-v1"
RUNS = ROOT / "artifacts" / "benchmark"


def manifest() -> dict:
    return json.loads((PACKAGE / "MANIFEST.json").read_text(encoding="utf-8"))


def run_root(model: str) -> Path:
    slug = "".join(c if c.isalnum() or c in "-._" else "-" for c in model)
    path = RUNS / PACKAGE.name / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def result(response) -> dict:
    return response.get("result") or {}


def connect(port: int) -> HostSession:
    return HostSession("127.0.0.1", port)


# ------------------------------------------------------------------- restore

def restore(session: HostSession, model: str) -> Path:
    """Replay the frozen A0 into a clean scene, and write this run's brief.

    Replayed rather than rebuilt: every model must start from the identical
    asset, and "constructed the same way" is not the same asset.
    """
    root = run_root(model)
    spec = json.loads((PACKAGE / "a0.json").read_text(encoding="utf-8"))
    for obj in result(session.call("scene.describe"))["objects"]:
        session.call("object.delete", {"object": obj["name"]})
    for entry in spec["objects"]:
        session.call("object.create",
                     {"kind": "cube", "name": entry["name"], "size": 1.0})
        session.call("object.transform", {"object": entry["name"],
                                          "scale": entry["scale"],
                                          "location": entry["location"]})

    raw = json.loads((PACKAGE / "brief.json").read_text(encoding="utf-8"))
    raw["subjects"] = [entry["name"] for entry in spec["objects"]]
    raw["references"] = [{**reference, "path": str(PACKAGE / reference["path"])}
                         for reference in raw["references"]]
    (root / "brief.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")

    fingerprint = result(session.call("scene.snapshot", {"level": "deep"}))["fingerprint"]
    (root / "a0-fingerprint.json").write_text(
        json.dumps({"fingerprint": fingerprint, "objects": raw["subjects"],
                    "restored_at": round(time.time(), 3)}, indent=2),
        encoding="utf-8")
    print(f"A0_RESTORED objects={len(raw['subjects'])} fingerprint={fingerprint}")
    return root


# ------------------------------------------------------------------- observe

def observe(session: HostSession, model: str, *, label: str = "packet") -> dict:
    """Exactly the evidence the participant receives. Nothing about the answer."""
    root = run_root(model)
    brief = load_brief(root / "brief.json")
    health = result(session.health())
    evidence = packet(measure(session, brief), brief)
    evidence["health"] = {
        "begin_correction": health["ready_for"]["begin_correction"]["status"],
        "mutate": health["ready_for"]["mutate"]["status"],
        "verified_rollback": health["ready_for"]["begin_correction"].get(
            "verified_rollback"),
    }
    # Recorded, never fed back: it exists so a later analysis can ask whether the
    # model chose better than a trivial ordering would have.
    evidence["advisory_ranking"] = advisory_ranking(evidence)
    (root / f"{label}.json").write_text(json.dumps(evidence, indent=2, default=str),
                                        encoding="utf-8")
    return evidence


# ------------------------------------------------------------------- attempt

def load_trajectory(root: Path, model: str) -> Trajectory:
    brief = load_brief(root / "brief.json")
    trajectory = Trajectory(root, brief, model)
    existing = root / "trajectory.json"
    if existing.is_file():
        prior = json.loads(existing.read_text(encoding="utf-8"))
        trajectory.attempts = prior.get("attempts", [])
        trajectory.manual_interventions = prior.get("manual_interventions", [])
    return trajectory


def attempt(session: HostSession, model: str, correction_path: Path) -> dict:
    root = run_root(model)
    trajectory = load_trajectory(root, model)
    budget = int(manifest()["budget"]["max_attempts"])
    if len(trajectory.attempts) >= budget:
        # Refused rather than warned. An attempt past the budget would still have
        # authored geometry, and the final asset would then be outside the
        # comparison whatever the record said afterwards.
        raise SystemExit(f"BUDGET_EXHAUSTED {len(trajectory.attempts)} of {budget}")

    raw = json.loads(correction_path.read_text(encoding="utf-8"))
    correction = Correction(
        objective=raw["objective"], reason=raw["reason"],
        targets=raw["targets"], protected=raw.get("protected", []),
        locality_targets=raw.get("locality", {}).get("targets", []),
        locality_protected=raw.get("locality", {}).get("protected", []),
        locality_allowed=raw.get("locality", {}).get("allowed", []),
        operations=raw["operations"], advisory_metrics=raw.get("advisory", []))

    record = run_correction(session, trajectory.brief, correction, trajectory)
    print(json.dumps({
        "attempt": record["attempt"],
        "n": len(trajectory.attempts), "budget": budget,
        "decision": record["evaluation"]["decision"],
        "outcome": record["outcome"],
        "restored": record["restored"],
        "targets_achieved": record["evaluation"]["targets_achieved"],
        "epsilon_audit": record["epsilon_audit"],
    }, indent=2, default=str))
    return record


# ------------------------------------------------------------------ finalize

def _vector(evidence: dict) -> dict:
    """The comparable numbers, flattened. Deliberately not summed.

    No scalar overall quality score is produced anywhere in this harness. A
    single number would let one metric's improvement pay for another's
    regression, which is the exact trade the vector acceptance rule exists to
    refuse.
    """
    vector = {
        "hard_invariants": evidence["hard_invariants"]["status"],
        "failing_invariants": evidence["hard_invariants"]["failing"],
        "dimensions": evidence["dimensions"]["size"],
        "coverage.observed_fraction": evidence["coverage"]["observed_fraction"],
    }
    for key, entry in (evidence.get("reference") or {}).items():
        for name, value in entry.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                vector[f"reference[{key}].{name}"] = value
    for key, entry in (evidence.get("patterns") or {}).items():
        vector[f"pattern[{key}].failing"] = entry.get("failing", [])
    return vector


def finalize(session: HostSession, model: str, tokens: str | None) -> dict:
    root = run_root(model)
    trajectory = load_trajectory(root, model)
    final = observe(session, model, label="packet-final")
    q0_path = root / "packet-q0.json"
    q0 = json.loads(q0_path.read_text(encoding="utf-8")) if q0_path.is_file() else None

    captures = capture_views(session, trajectory.brief, root / "captures", "final")
    decisions = [a["evaluation"]["decision"] for a in trajectory.attempts]
    report = {
        "benchmark": manifest()["benchmark"],
        "model": model,
        # Not observable from inside the harness. Recorded as self-reported or
        # left null rather than estimated, because an invented token count would
        # be the easiest number in the whole comparison to be wrong about.
        "token_usage": {"value": tokens, "source": "self-reported by the operator"}
        if tokens else {"value": None, "source": "not observable from the harness"},
        "attempts": {
            "total": len(trajectory.attempts),
            "budget": manifest()["budget"]["max_attempts"],
            "accepted": decisions.count("accept"),
            "rejected": decisions.count("reject"),
            "indeterminate": decisions.count("indeterminate"),
        },
        "rollback_proofs": [
            {"attempt": a["attempt"], "outcome": a["outcome"],
             "begin_fingerprint": a.get("begin_fingerprint"),
             "final_fingerprint": a.get("final_fingerprint"),
             "restored": a.get("restored")}
            for a in trajectory.attempts],
        "tool_calls": sum(a.get("tool_calls", 0) for a in trajectory.attempts),
        "elapsed_seconds": round(sum(a.get("elapsed_seconds", 0.0)
                                     for a in trajectory.attempts), 3),
        "manual_interventions": trajectory.manual_interventions,
        "stop_reason": trajectory.stop_reason,
        "q0_vector": _vector(q0) if q0 else None,
        "final_vector": _vector(final),
        "captures": captures,
        "known_limits": manifest()["known_limits"],
        "note": "no scalar quality score is produced; the vectors are compared "
                "component by component",
    }
    (root / "report.json").write_text(json.dumps(report, indent=2, default=str),
                                      encoding="utf-8")
    print(json.dumps(report["attempts"], indent=2))
    print(f"BENCHMARK_REPORT {root / 'report.json'}")
    return report


# ---------------------------------------------------------------------- main

def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    command, model = sys.argv[1], sys.argv[2]
    rest = sys.argv[3:]
    port = 9877
    if "--port" in rest:
        port = int(rest[rest.index("--port") + 1])
    tokens = rest[rest.index("--tokens") + 1] if "--tokens" in rest else None

    session = connect(port)
    try:
        if command == "restore":
            restore(session, model)
            evidence = observe(session, model, label="packet-q0")
            print(json.dumps(evidence, indent=2, default=str))
        elif command == "observe":
            print(json.dumps(observe(session, model), indent=2, default=str))
        elif command == "attempt":
            attempt(session, model, Path(rest[0]))
        elif command == "finalize":
            finalize(session, model, tokens)
        else:
            print(f"unknown command: {command}")
            return 2
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
