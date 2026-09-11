"""Derive `rvbench:correction-transfer/v3-feedback` from v2's frozen manifest.

v3a is an ablation, and an ablation is only worth running if exactly one thing
changed. So this does not regenerate anything. It copies v2's participant files
byte-for-byte, carries v2's normalized A0 signature, Q0 vector and hidden
commitments across unchanged, reuses v2's private restore recipe by name, and
sets a single harness flag: `return_failure_causes`.

That flag is the whole experiment. v2's `submit_correction` returned the decision,
`targets_achieved` and the epsilon audit, but never `reject_causes` or
`indeterminate_causes` — so a participant could see what had worked inside a
rejected candidate but not why the candidate was refused, even though
`CHALLENGE.md` promised "the evidence from the failure". v3a delivers the
evaluator's existing record. Nothing else moves, so the difference between a
model's v2 and v3a runs is attributable to being told why it failed.

Deliberately unchanged, and recorded as such in the manifest:

- `CHALLENGE.md`, byte-identical, including its v2 heading. It already promised
  the feedback; v3a is the harness keeping a promise the document had made.
- the tool surface and every tool description.
- the asset, the faults, the references, the budget, the DAT policy.
- the metric namespace, the invariant/metric separation, error wording, the
  epsilon rule. Those are v3b and later, so that their effect can be measured
  separately rather than folded into this one.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks" / "correction-transfer-v2"
TARGET = ROOT / "benchmarks" / "correction-transfer-v3-feedback"

BENCHMARK = "rvbench:correction-transfer/v3-feedback"
PARTICIPANT_FILES = ("CHALLENGE.md", "brief.json",
                     "reference-front.png", "reference-side.png")

# Hashed the same way the v2 manifest was, so a digest here can be compared with
# one there directly: text with line endings normalised, because git rewrites them.
TEXT = (".md", ".json", ".py", ".txt")


def digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix in TEXT:
        data = data.replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def main() -> int:
    v2 = json.loads((SOURCE / "MANIFEST.json").read_text(encoding="utf-8"))
    TARGET.mkdir(parents=True, exist_ok=True)

    for name in PARTICIPANT_FILES:
        shutil.copyfile(SOURCE / name, TARGET / name)
        # Byte-identical, not merely equivalent: a participant file that differed
        # from v2's would be a second variable.
        if (SOURCE / name).read_bytes() != (TARGET / name).read_bytes():
            raise SystemExit(f"{name} is not byte-identical to v2")

    carried = ("budget", "disclosed_to_participant", "withheld_from_participant",
               "known_limits", "required_invariants", "a0_construction_disclosed",
               "normalized_a0_signature", "normalized_a0", "q0_vector",
               "reproducibility_claim", "hidden_commitments", "record_per_run",
               "comparison_rules")
    manifest = {
        "benchmark": BENCHMARK,
        "status": {
            "label": "v3a failure-feedback ablation",
            "note": "identical to rvbench:correction-transfer/v2 in asset, faults, "
                    "references, budget, tools and participant files; the only "
                    "functional difference is that submit_correction returns the "
                    "evaluator's failure causes",
        },
        "derived_from": {"benchmark": v2["benchmark"],
                         "manifest": digest(SOURCE / "MANIFEST.json")},
        "harness": {"return_failure_causes": True},
        "harness_change_from_v2": (
            "submit_correction returns an `evaluation` block containing the "
            "evaluator's reject_causes, indeterminate_causes, targets_achieved and "
            "invariants_checked, as already computed. Indeterminate causes are "
            "returned even when a reject cause decided the outcome."),
        "unchanged_from_v2": [
            "CHALLENGE.md, byte-identical including its heading",
            "brief.json and both reference images, byte-identical",
            "the tool surface and every tool description",
            "the asset, faults, references, attempt budget and DAT policy",
            "the metric namespace, invariant/metric separation, error wording and "
            "epsilon rule — deferred to later versions so each effect is measured "
            "separately"],
        # The answer key is v2's, reused by name. A second copy would be a second
        # thing that could drift, and only one would be covered by the commitment.
        "private_material": SOURCE.name,
        **{key: v2[key] for key in carried},
        "files": {name: digest(TARGET / name) for name in PARTICIPANT_FILES},
    }
    for name in PARTICIPANT_FILES:
        if manifest["files"][name] != v2["files"][name]:
            raise SystemExit(f"{name} digest differs from v2's published digest")

    (TARGET / "MANIFEST.json").write_text(json.dumps(manifest, indent=2),
                                          encoding="utf-8")
    print(f"V3_FEEDBACK_READY {TARGET} derived_from={v2['benchmark']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
