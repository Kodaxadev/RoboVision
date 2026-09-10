"""Run one model-chosen correction against a brief, and record everything.

The correction arrives as a JSON file: an objective, the metrics it targets, the
metrics it protects, the locality it declares, and the typed operations to
perform. That file is the entire interface between the reasoning client and
RoboVision — no prose reaches the driver, and the driver contributes no
judgement. Any frontier model able to emit this JSON can drive the loop.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from observe_support import load_brief  # noqa: E402
from robovision.artist_loop_run import Correction, Trajectory, run_correction  # noqa: E402
from robovision.session import HostSession  # noqa: E402


def main() -> int:
    brief_path = Path(sys.argv[1])
    correction_path = Path(sys.argv[2])
    model = sys.argv[3] if len(sys.argv) > 3 else "unknown"
    port = int(sys.argv[4]) if len(sys.argv) > 4 else 9877

    brief = load_brief(brief_path)
    raw = json.loads(correction_path.read_text(encoding="utf-8"))
    correction = Correction(
        objective=raw["objective"], reason=raw["reason"],
        targets=raw["targets"], protected=raw.get("protected", []),
        locality_targets=raw.get("locality", {}).get("targets", []),
        locality_protected=raw.get("locality", {}).get("protected", []),
        locality_allowed=raw.get("locality", {}).get("allowed", []),
        operations=raw["operations"],
        advisory_metrics=raw.get("advisory", []))

    root = brief_path.parent
    trajectory = Trajectory(root, brief, model)
    existing = root / "trajectory.json"
    if existing.is_file():
        prior = json.loads(existing.read_text(encoding="utf-8"))
        trajectory.attempts = prior.get("attempts", [])
        trajectory.manual_interventions = prior.get("manual_interventions", [])

    session = HostSession("127.0.0.1", port)
    try:
        record = run_correction(session, brief, correction, trajectory)
    finally:
        session.close()

    print(json.dumps({
        "attempt": record["attempt"],
        "decision": record["evaluation"]["decision"],
        "outcome": record["outcome"],
        "restored": record["restored"],
        "targets_achieved": record["evaluation"]["targets_achieved"],
        "reject_causes": record["evaluation"]["reject_causes"],
        "indeterminate_causes": record["evaluation"]["indeterminate_causes"],
        "epsilon_audit": record["epsilon_audit"],
        "operation_failure": record["operation_failure"],
        "tool_calls": record["tool_calls"],
        "elapsed_seconds": record["elapsed_seconds"],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
