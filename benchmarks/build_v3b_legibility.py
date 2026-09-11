"""Derive `rvbench:correction-transfer/v3b-legibility` from v3a's frozen manifest.

v3b is the second ablation in the sequence, and like v3a it changes exactly one
thing. It copies v3a's participant files byte-for-byte, carries v3a's normalized A0
signature, Q0 vector, hidden commitments and failure-feedback behaviour across
unchanged, reuses the v2 answer key by name, and adds a single harness flag:
`metric_addresses`.

That flag is the whole experiment. With it, every metric the discrepancy packet
displays carries, beside its friendly field, the exact `{metric, kind}` pair a
correction must name — so what a participant reads is what it can submit. The
question it answers is mechanistic: does a model that spent v3a guessing metric
identifiers now copy the canonical address, reach geometric evaluation sooner, and
spend its budget on the asset rather than on the protocol?

Deliberately unchanged, and recorded as such in the manifest: the asset, faults,
references, budget, evaluator, correction semantics, tool surface, prompt,
`CHALLENGE.md` (byte-identical, still headed v2), failure feedback, invariant
presentation, missing-metric wording, the epsilon rule and protection behaviour.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks" / "correction-transfer-v3-feedback"
TARGET = ROOT / "benchmarks" / "correction-transfer-v3b-legibility"

BENCHMARK = "rvbench:correction-transfer/v3b-legibility"
PARTICIPANT_FILES = ("CHALLENGE.md", "brief.json",
                     "reference-front.png", "reference-side.png")
TEXT = (".md", ".json", ".py", ".txt")

# Fields that describe v3a itself and are replaced rather than carried.
NOT_CARRIED = ("benchmark", "status", "derived_from", "harness",
               "unchanged_from_v2", "files")


def digest(path: Path) -> str:
    data = path.read_bytes()
    if path.suffix in TEXT:
        data = data.replace(b"\r\n", b"\n")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def main() -> int:
    v3a = json.loads((SOURCE / "MANIFEST.json").read_text(encoding="utf-8"))
    if not (v3a.get("harness") or {}).get("return_failure_causes"):
        raise SystemExit("v3a manifest does not carry the failure-feedback flag")
    TARGET.mkdir(parents=True, exist_ok=True)

    for name in PARTICIPANT_FILES:
        shutil.copyfile(SOURCE / name, TARGET / name)
        if (SOURCE / name).read_bytes() != (TARGET / name).read_bytes():
            raise SystemExit(f"{name} is not byte-identical to v3a")

    manifest = {
        "benchmark": BENCHMARK,
        "status": {
            "label": "v3b metric-legibility ablation",
            "note": "identical to rvbench:correction-transfer/v3-feedback in asset, "
                    "faults, references, budget, evaluator, tools, participant "
                    "files and failure feedback; the only functional difference is "
                    "that the discrepancy packet carries each displayed metric's "
                    "canonical {metric, kind} address",
        },
        "derived_from": {"benchmark": v3a["benchmark"],
                         "manifest": digest(SOURCE / "MANIFEST.json")},
        "harness": {"return_failure_causes": True, "metric_addresses": True},
        "harness_change_from_v3a": (
            "each section of the discrepancy packet gains an `addresses` map from "
            "friendly field to the exact {metric, kind} pair a correction's "
            "targets or protected entry takes. Additive only; an address is shown "
            "only if the evaluator resolves it to the displayed value."),
        "unchanged_from_v3a": [
            "CHALLENGE.md, brief.json and both reference images, byte-identical",
            "the tool surface and every tool description",
            "the asset, faults, references, attempt budget, evaluator and "
            "correction semantics",
            "failure feedback: reject and indeterminate causes are still returned",
            "invariant presentation, missing-metric wording, the epsilon rule and "
            "protection behaviour — each deferred to its own ablation"],
        **{key: value for key, value in v3a.items() if key not in NOT_CARRIED},
        "files": {name: digest(TARGET / name) for name in PARTICIPANT_FILES},
    }
    for name in PARTICIPANT_FILES:
        if manifest["files"][name] != v3a["files"][name]:
            raise SystemExit(f"{name} digest differs from v3a's published digest")

    (TARGET / "MANIFEST.json").write_text(json.dumps(manifest, indent=2),
                                          encoding="utf-8")
    print(f"V3B_LEGIBILITY_READY {TARGET} derived_from={v3a['benchmark']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
