"""Run one participant through a frozen correction-transfer benchmark.

Operator side. The participant never touches this file — it reads what the facade
returns and emits correction JSON, and the harness executes that JSON exactly as
the Artist Loop defines it.

    restore  <bench> <model>          restore A0, verify it, record Q0
    observe  <bench> <model>          the discrepancy packet
    attempt  <bench> <model> <file>   one correction, within budget
    stop     <bench> <model> <reason> end the run deliberately
    finalize <bench> <model>          vectors, captures, the run record

The restore recipe for a blind benchmark is *not* in the participant package. It
is read from the private directory named by `RVBENCH_PRIVATE`, which lives
outside the public repository — because the repository is public and a file
committed to it is not withheld from anyone.

Every restore is verified twice over: the normalized-authoring signature must
match the frozen value, and Q0 must match the frozen Q0. Neither is a formality.
A restore that silently differed would make two participants' results
incomparable while every number still looked reasonable.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.signature import collect, differences, signature  # noqa: E402
from robovision.artist_loop import Brief, advisory_ranking, measure, packet  # noqa: E402
from benchmarks.report import finalize, vector  # noqa: E402
from benchmarks.results import (  # noqa: E402,F401 - re-exported
    FAILURE_FIELDS, LIFECYCLE_FILES, expected_state, participant_result)
from robovision.artist_loop_run import (  # noqa: E402
    Correction, Trajectory, run_correction)
from robovision.session import HostSession  # noqa: E402

RUNS = ROOT / "artifacts" / "benchmark"

# The reasons a run may end. Free text is kept alongside, never instead: a set of
# categories is what makes ten runs comparable, and the note is what makes one
# run understandable.
STOP_REASONS = {
    "budget_exhausted": "the attempt budget was spent",
    "no_worthwhile_correction_remains": "no measured discrepancy remains that the "
                                        "participant believes it can improve above "
                                        "metric resolution",
    "cannot_infer_safe_correction": "a discrepancy is visible but no correction "
                                    "could be constructed that the participant "
                                    "believed was safe",
    "evidence_insufficient": "the available measurements do not localise the "
                             "problem well enough to act on",
    "repeated_rejection": "the same objective was rejected repeatedly and the "
                          "participant chose to stop rather than keep spending",
    "other": "an explicit reason given in the note",
}


def _result(response) -> dict:
    return response.get("result") or {}


def private_dir() -> Path:
    raw = os.environ.get("RVBENCH_PRIVATE")
    if not raw:
        raise SystemExit(
            "RVBENCH_PRIVATE is not set. The restore recipe and hidden material "
            "for a blind benchmark live outside this repository; point it at that "
            "directory.")
    return Path(raw)


class Runner:
    """One benchmark, one model, one run directory."""

    def __init__(self, session: HostSession, benchmark: str, model: str) -> None:
        self.session = session
        self.package = ROOT / "benchmarks" / benchmark
        self.manifest = json.loads(
            (self.package / "MANIFEST.json").read_text(encoding="utf-8"))
        slug = "".join(c if c.isalnum() or c in "-._" else "-" for c in model)
        self.root = RUNS / benchmark / slug
        self.root.mkdir(parents=True, exist_ok=True)
        self.model = model
        self._observation_calls = 0

    def take_observation_calls(self) -> int:
        spent, self._observation_calls = self._observation_calls, 0
        return spent

    @property
    def _calls_path(self) -> Path:
        return self.root / "calls.json"

    def spend_observation(self, count: int) -> None:
        """Persisted, because each subcommand is its own process.

        A counter that lived only in memory would report the last invocation's
        calls as the run's total, which is worse than not counting at all.
        """
        current = self.calls()
        current["participant_observation_tool_calls"] += count
        self._calls_path.write_text(json.dumps(current, indent=2), encoding="utf-8")

    def calls(self) -> dict:
        if self._calls_path.is_file():
            return json.loads(self._calls_path.read_text(encoding="utf-8"))
        return {"participant_observation_tool_calls": 0}

    # --------------------------------------------------------------- restore

    def _spec(self) -> dict:
        """Where A0 comes from: the package if disclosed, the private dir if not."""
        disclosed = self.manifest.get("a0_construction_disclosed")
        if disclosed:
            return json.loads((self.package / "a0.json").read_text(encoding="utf-8"))
        # A benchmark derived from another reuses its answer key by name rather
        # than holding a copy: two copies of a sealed recipe are two things that
        # can drift, and only one of them is covered by the published hash.
        material = self.manifest.get("private_material", self.package.name)
        path = private_dir() / material / "restore.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _brief_json(self) -> dict:
        raw = json.loads((self.package / "brief.json").read_text(encoding="utf-8"))
        raw["references"] = [{**reference, "path": str(self.package / reference["path"])}
                             for reference in raw["references"]]
        return raw

    def brief(self) -> Brief:
        raw = self._brief_json()
        return Brief(name=raw["name"], subjects=raw["subjects"],
                     references=raw["references"], patterns=raw["patterns"],
                     protected_objects=raw["protected_objects"],
                     required_invariants=raw["required_invariants"],
                     max_dimension=raw.get("max_dimension"), notes=raw.get("notes", ""))

    def restore(self) -> dict:
        # A run identity names one experiment. Restoring over one that already
        # holds attempts, a stop or a final report would put a fresh A0 under a
        # record describing something else. A new experiment gets a new run id.
        used = [name for name in LIFECYCLE_FILES if (self.root / name).is_file()]
        if used:
            raise SystemExit(
                f"RUN_ID_IN_USE {self.root.name}: already holds {', '.join(used)}. "
                f"A new experiment needs a new run identity.")
        spec = self._spec()
        for obj in _result(self.session.call("scene.describe"))["objects"]:
            self.session.call("object.delete", {"object": obj["name"]})
        for entry in spec["objects"]:
            self.session.call("object.create",
                              {"kind": "cube", "name": entry["name"], "size": 1.0})
            self.session.call("object.transform",
                              {"object": entry["name"], "scale": entry["scale"],
                               "location": entry["location"],
                               "rotation": entry.get("rotation", [0.0, 0.0, 0.0])})
        (self.root / "brief.json").write_text(
            json.dumps(self._brief_json(), indent=2), encoding="utf-8")
        return self.verify()

    def verify(self) -> dict:
        """The restore is the same authored asset every participant gets.

        Not "byte-identical": Blender issues fresh RoboVision identities on every
        create, so two correct restores differ in every UUID and every revision.
        They are *normalized-authoring equivalent*, which is the property the
        benchmark actually needs, and this proves it rather than asserting it.
        """
        expected = self.manifest.get("normalized_a0_signature")
        actual = signature(self.session)
        if expected is None:
            # v1 predates the signature and is kept as frozen harness-validation
            # evidence rather than retrofitted. Saying the check did not happen
            # beats reporting a pass that was never performed.
            report = {"expected": None, "actual": actual, "equivalent": True,
                      "verified": False,
                      "note": "this package publishes no frozen normalized "
                              "signature, so the restore was recorded but not "
                              "checked"}
            (self.root / "restore-verification.json").write_text(
                json.dumps(report, indent=2), encoding="utf-8")
            return report
        report = {"expected": expected, "actual": actual,
                  "equivalent": actual == expected, "verified": True}
        if not report["equivalent"]:
            frozen = self.manifest.get("normalized_a0")
            report["differences"] = (differences(frozen, collect(self.session))
                                     if frozen else ["frozen collection not published"])
        (self.root / "restore-verification.json").write_text(
            json.dumps({**report, "at": round(time.time(), 3)}, indent=2),
            encoding="utf-8")
        return report

    # --------------------------------------------------------------- observe

    def observe(self, label: str = "packet") -> dict:
        brief = self.brief()
        health = _result(self.session.health())
        bundle = measure(self.session, brief)
        evidence = packet(bundle, brief)
        evidence["health"] = {
            "begin_correction": health["ready_for"]["begin_correction"]["status"],
            "mutate": health["ready_for"]["mutate"]["status"],
            "verified_rollback": health["ready_for"]["begin_correction"].get(
                "verified_rollback"),
        }
        # Recorded, never fed back into the decision.
        evidence["advisory_ranking"] = advisory_ranking(evidence)
        # One health call, one scene.describe inside measure, one call per
        # certificate. Counted so the report can separate looking from acting.
        self.spend_observation(2 + len(bundle))
        self._observation_calls += 2 + len(bundle)
        (self.root / f"{label}.json").write_text(
            json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        return evidence

    # --------------------------------------------------------------- attempt

    def trajectory(self) -> Trajectory:
        trajectory = Trajectory(self.root, self.brief(), self.model)
        existing = self.root / "trajectory.json"
        if existing.is_file():
            prior = json.loads(existing.read_text(encoding="utf-8"))
            trajectory.attempts = prior.get("attempts", [])
            trajectory.manual_interventions = prior.get("manual_interventions", [])
            trajectory.stop_reason = prior.get("summary", {}).get("stop_reason")
        return trajectory

    def identity(self) -> dict:
        """Which experiment this process is serving. Not secret, and not a pin.

        Returned to the participant with `health` so a misrouted session is
        visible at once — the 2026-09-10 incident was a participant calling a
        shim that belonged to a different, finished run, and nothing in any
        response said so.
        """
        trajectory = self.trajectory()
        return {"benchmark": self.manifest["benchmark"], "run": self.root.name,
                "attempts_used": len(trajectory.attempts),
                "stopped": trajectory.stop_reason is not None}

    def expected_scene(self) -> dict:
        """What the editor must currently hold for this run to continue."""
        return expected_state(self.trajectory().attempts,
                              self.manifest.get("normalized_a0_signature"))

    def bind(self) -> dict:
        """Refuse to act on a scene that is not this run's.

        One Blender scene serves every run in turn. Nothing previously checked
        that the scene a run was about to act on was the one that run had left
        — only that a restore file existed. A shim started for one run while
        the scene held another's state would have evaluated corrections against
        the wrong asset and recorded them under the wrong identity.
        """
        expected = self.expected_scene()
        if expected["kind"] == "unknown":
            raise SystemExit(f"SCENE_BINDING_UNKNOWN {self.root.name}: "
                             f"{expected['reason']}")
        if expected["kind"] == "signature":
            actual = signature(self.session)
        else:
            actual = _result(self.session.call(
                "scene.snapshot", {"level": "deep"}))["fingerprint"]
        if actual != expected["value"]:
            raise SystemExit(
                f"SCENE_NOT_BOUND_TO_RUN {self.root.name}: the editor does not "
                f"hold this run's current state ({expected['kind']} expected "
                f"{expected['value']}, found {actual}). Another run may have "
                f"used the scene since.")
        return {"bound": True, **expected}

    def attempt(self, raw: dict) -> dict:
        trajectory = self.trajectory()
        if trajectory.stop_reason:
            raise SystemExit(f"RUN_STOPPED {trajectory.stop_reason}")
        # Checked before the budget and before any transaction: a correction
        # evaluated against another run's scene is worse than no correction.
        self.bind()
        budget = int(self.manifest["budget"]["max_attempts"])
        if len(trajectory.attempts) >= budget:
            # Refused rather than warned: an attempt past the budget would still
            # author geometry, and the final asset would then sit outside the
            # comparison whatever the record said afterwards.
            raise SystemExit(f"BUDGET_EXHAUSTED {len(trajectory.attempts)} of {budget}")

        correction = Correction(
            objective=raw["objective"], reason=raw["reason"],
            targets=raw["targets"], protected=raw.get("protected", []),
            locality_targets=raw.get("locality", {}).get("targets", []),
            locality_protected=raw.get("locality", {}).get("protected", []),
            locality_allowed=raw.get("locality", {}).get("allowed", []),
            operations=raw["operations"], advisory_metrics=raw.get("advisory", []))
        record = run_correction(self.session, trajectory.brief, correction, trajectory)
        if len(trajectory.attempts) >= budget:
            self.stop("budget_exhausted", "the harness ended the run")
        return participant_result(
            record, n=len(trajectory.attempts), budget=budget,
            evidence=self.observe(label="packet-latest"),
            return_causes=bool(
                (self.manifest.get("harness") or {}).get("return_failure_causes")))

    def stop(self, reason: str, note: str = "") -> dict:
        if reason not in STOP_REASONS:
            raise SystemExit(f"UNKNOWN_STOP_REASON {reason}; "
                             f"one of {sorted(STOP_REASONS)}")
        trajectory = self.trajectory()
        # A stopped run is immutable. This was not enforced: a second stop simply
        # overwrote the first, and on 2026-09-10 a participant session misrouted
        # to a finished run replaced that run's sealed stop reason and note with
        # its own. `attempt` already refused a stopped run; `stop` now does too,
        # and says what the original reason was rather than changing it.
        if trajectory.stop_reason:
            raise SystemExit(f"RUN_STOPPED {trajectory.stop_reason}")
        trajectory.stop_reason = reason
        trajectory.write()
        (self.root / "stop.json").write_text(
            json.dumps({"reason": reason, "meaning": STOP_REASONS[reason],
                        "note": note, "at": round(time.time(), 3)}, indent=2),
            encoding="utf-8")
        return {"stopped": reason, "note": note}


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    command, benchmark, model = sys.argv[1], sys.argv[2], sys.argv[3]
    rest = sys.argv[4:]
    port = int(rest[rest.index("--port") + 1]) if "--port" in rest else 9877
    tokens = rest[rest.index("--tokens") + 1] if "--tokens" in rest else None
    note = rest[rest.index("--note") + 1] if "--note" in rest else ""

    session = HostSession("127.0.0.1", port)
    try:
        runner = Runner(session, benchmark, model)
        if command == "restore":
            report = runner.restore()
            if not report["equivalent"]:
                print(json.dumps(report, indent=2))
                raise SystemExit("A0_RESTORE_MISMATCH: refusing to begin the run")
            # The second, independent check. The signature says the authored
            # state matches; this says the *measurements* of it do. They can
            # disagree — a measurement that changed underneath the benchmark
            # would leave the geometry identical and every number different —
            # and a comparison across participants needs both to hold.
            evidence = runner.observe(label="packet-q0")
            frozen = runner.manifest.get("q0_vector")
            if not report.get("verified"):
                print("WARNING: this package publishes no frozen signature; the "
                      "restore was not verified")
            measured = vector(evidence)
            drift = {key: [frozen.get(key), measured.get(key)]
                     for key in sorted(set(frozen) | set(measured))
                     if frozen.get(key) != measured.get(key)} if frozen else {}
            report["q0_matches_frozen"] = not drift
            report["q0_drift"] = drift
            (runner.root / "restore-verification.json").write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8")
            if drift:
                print(json.dumps(drift, indent=2, default=str))
                raise SystemExit("Q0_MISMATCH: refusing to begin the run")
            print(f"A0_RESTORED signature={report['actual']} q0=frozen")
            print(json.dumps(evidence, indent=2, default=str))
        elif command == "verify":
            print(json.dumps(runner.verify(), indent=2))
        elif command == "observe":
            print(json.dumps(runner.observe(), indent=2, default=str))
        elif command == "attempt":
            raw = json.loads(Path(rest[0]).read_text(encoding="utf-8"))
            print(json.dumps(runner.attempt(raw), indent=2, default=str))
        elif command == "stop":
            print(json.dumps(runner.stop(rest[0], note), indent=2))
        elif command == "finalize":
            report = finalize(runner, tokens)
            print(json.dumps(report["attempts"], indent=2))
            print(f"BENCHMARK_REPORT {runner.root / 'report.json'}")
        else:
            print(f"unknown command: {command}")
            return 2
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
