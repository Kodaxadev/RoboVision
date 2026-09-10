"""Freeze the correction-transfer benchmark package. Harness side, run once.

The package this produces is the *entire* thing a participating model receives,
alongside a live RoboVision session. It is deliberately a directory rather than a
repository checkout, because most of what must be withheld is withheld by not
being in it: the blockout generator that produced the reference silhouettes, the
proxy seed, the fixture code, and every previous model's trajectory.

What is frozen and what is withheld are both written into the manifest, so a
reader of a result can check the claim rather than take it. A benchmark whose
disclosure boundary lives only in the head of the person who built it is not one.

This script does not build geometry. A0 is a recorded operation list replayed
into a clean scene, so every model starts from a byte-identical asset — which is
the whole premise of a transfer experiment and cannot be achieved by rebuilding
something "the same way".
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "artifacts" / "artist-loop" / "frontier"
PACKAGE = ROOT / "benchmarks" / "correction-transfer-v1"

BENCHMARK = "rvbench:correction-transfer/v1"

# v1 is kept, unaltered, as proof that the benchmark machinery works end to end.
# It is not a blind benchmark and must not be presented as one: the RoboVision
# repository is public, and the blockout generator, the proxy seed and the
# fixture code that produced these references are committed in it. A
# network-enabled model could simply fetch them. Not handing a participant a
# checkout is not secrecy, and saying otherwise would be the kind of claim this
# project exists to avoid making.
STATUS = {
    "label": "harness validation / not suitable as a blind network-enabled benchmark",
    "reason": "the challenge-generation material for v1 is committed in a public "
              "repository, so the withheld boundary is not real against a model "
              "with web or GitHub access",
    "superseded_by": "rvbench:correction-transfer/v2",
    "kept_because": "it is the evidence that the harness, the strict delivery "
                    "path, the evaluator and the rollback proofs work",
}

# Frozen before the first independent run and not tunable per model. Changing
# any of these is a new benchmark version, not an adjustment.
BUDGET = {
    "max_attempts": 8,
    "attempt_counts": "accepted, rejected and indeterminate attempts all count",
    "epsilon_policy": "the model declares an epsilon per target; the harness "
                      "audits it against the metric's own resolution and records "
                      "any improvement below that resolution as unproven",
    "tolerance_policy": "protected metrics use the tolerances the model declares; "
                        "the harness does not relax them on rejection",
}

WITHHELD = [
    "the blockout generator that produced the reference silhouettes",
    "the proxy seed and any sealed ground truth",
    "fixture-generation implementation of any kind",
    "target dimensions or proportions not stated in the brief",
    "the RoboVision repository source, which contains all of the above",
    "any previous model's trajectory, correction JSON or reasoning",
]

DISCLOSED = [
    "the written asset brief, including the declared overall envelope",
    "the two reference silhouettes and the frame each was captured in",
    "the frozen A0, live and inspectable through RoboVision",
    "a0.json: the exact operation list that constructs A0 — disclosed, "
    "because it is in the package",
    "the public RoboVision tool surface and its schemas",
    "system.health",
    "authoritative observations and the DAT discrepancy packet",
    "shaded viewport captures through the normal public capture tools",
]

# Honest and deliberately disclosed: the reference is a blockout, so no reference
# metric can ever reach zero on an asset that has fin detail. Withholding this
# would not test a skill, it would make every model chase a target that does not
# exist and would report a measurement artefact as a difference between models.
KNOWN_LIMITS = [
    "The references depict a coarse blockout massing. They constrain the outer "
    "silhouette only and contain no cooling-stack detail, so a correct asset "
    "still shows residual front excess against them. Reference metrics measure "
    "agreement with the massing, not perfection.",
    "reference.subject_set is unmeasured for these references: they were not "
    "produced by truth.silhouette over the participant's own objects, so the "
    "comparison cannot verify that the image depicts the same parts.",
    "Coverage measures inspectability, not quality. A low observed fraction is "
    "not by itself a defect to correct.",
]


# Text files are hashed with line endings normalised to LF. Git rewrites them on
# checkout, so a digest over raw bytes would differ between the machine that
# froze the package and a participant's clone of it — and a commitment that fails
# for a reason unrelated to content is worse than none.
TEXT = (".md", ".json", ".py", ".txt")


def digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix in TEXT:
        data = data.replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def public_brief(raw: dict) -> dict:
    """The brief with every trace of the previous run's reasoning removed.

    `a0_source` and `a0_reasoning` record which model chose the massing and why.
    That is precisely a previous model's trajectory, and it would tell the next
    participant where the author already believed the asset was weak.
    """
    brief = {key: value for key, value in raw.items()
             if key not in ("a0_source", "a0_reasoning")}
    # Paths are rewritten relative to the package, because an absolute path from
    # the machine that built it is both useless and a small information leak.
    brief["references"] = [
        {**reference, "path": Path(reference["path"]).name}
        for reference in brief["references"]]
    brief["known_limits"] = KNOWN_LIMITS
    return brief


def main() -> int:
    PACKAGE.mkdir(parents=True, exist_ok=True)
    raw = json.loads((SOURCE / "brief.json").read_text(encoding="utf-8"))
    brief = public_brief(raw)
    (PACKAGE / "brief.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")

    for reference in raw["references"]:
        shutil.copyfile(Path(reference["path"]), PACKAGE / Path(reference["path"]).name)

    # A0 as an operation list, replayed rather than rebuilt.
    a0 = json.loads((SOURCE / "a0-build.json").read_text(encoding="utf-8"))
    frozen = {"objects": a0["objects"],
              "source": "recorded operation list; replayed into a clean scene so "
                        "every participant starts from a byte-identical asset",
              "note": "who authored A0 is deliberately not stated here"}
    (PACKAGE / "a0.json").write_text(json.dumps(frozen, indent=2), encoding="utf-8")

    # CHALLENGE.md is hashed with everything else: it is the participant-facing
    # contract, and a package whose rules can drift without changing its digest
    # is not frozen.
    files = sorted(p for p in PACKAGE.iterdir()
                   if p.is_file() and p.name != "MANIFEST.json")
    manifest = {
        "benchmark": BENCHMARK,
        "status": STATUS,
        "task": "correction transfer: improve a frozen flawed asset using "
                "RoboVision evidence. Creation ability is a separate experiment "
                "and is not measured here.",
        "budget": BUDGET,
        # `a0.json` is physically in the participant directory, so it is declared
        # disclosed. A package that shipped the exact construction recipe while a
        # manifest called it withheld would be worse than not claiming a boundary
        # at all. v2 keeps its restore recipe outside the repository instead.
        "a0_construction_disclosed": True,
        "disclosed_to_participant": DISCLOSED,
        "withheld_from_participant": WITHHELD,
        "known_limits": KNOWN_LIMITS,
        "required_invariants": brief["required_invariants"],
        "record_per_run": [
            "model identity and version", "accepted / rejected / indeterminate",
            "rollback proofs (begin and final fingerprints per attempt)",
            "tool calls", "token usage where observable", "elapsed seconds",
            "DAT Q0 and final vectors", "hard invariants",
            "shaded visual captures", "manual interventions", "stop reason"],
        "comparison_rules": [
            "identical budget, tools, DAT policy and references for every model",
            "epsilon and tolerances are never tuned per model",
            "if a real DAT metric defect is found, stop the comparison, fix it "
            "with an independent regression test, version the benchmark and "
            "restart affected runs — never patch mid-comparison",
            "no scalar overall quality score is produced",
        ],
        "files": {path.name: digest(path) for path in files},
    }
    (PACKAGE / "MANIFEST.json").write_text(json.dumps(manifest, indent=2),
                                           encoding="utf-8")
    print(f"BENCHMARK_PACKAGE_READY {PACKAGE} files={len(files) + 1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
