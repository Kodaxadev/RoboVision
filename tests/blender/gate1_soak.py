"""Gate 1 repeatability: one passing run proves almost nothing.

The loop below runs the full observe -> mutate -> verify -> roll back -> prove
cycle hundreds of times against one long-lived Blender session and one
long-lived host runtime, because the failures worth catching here are the ones
that only appear after repetition: leaked datablocks, undo stacks that drift,
identity registries that grow, revisions that stop advancing, and fingerprints
that are not actually deterministic.

Cycle count comes from ROBOVISION_SOAK_CYCLES so CI and local runs can differ.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402

CYCLES = max(1, int(os.environ.get("ROBOVISION_SOAK_CYCLES", "200")))


def one_cycle(host: Host, index: int, baseline_fingerprint: str) -> None:
    """A complete transactional edit that must leave no trace behind."""
    revision_before = host.call("scene.describe")["revision"]

    tx = host.result("transaction.begin", {"label": f"soak-{index}"})
    expect(
        tx["begin_fingerprint"] == baseline_fingerprint,
        f"cycle {index} began from a scene that had drifted: {tx['begin_fingerprint']}",
    )
    tx_id = tx["transaction"]

    created = host.result("object.create", {"kind": "cube", "name": f"Soak{index}"})
    oid = created["id"]

    mesh_revision = host.result("mesh.inspect", {"object": oid})["mesh_revision"]
    extruded = host.result(
        "mesh.extrude_faces",
        {
            "object": oid,
            "face_indices": [0],
            "translation": [0.0, 0.0, 0.75],
            "expected_mesh_revision": mesh_revision,
        },
    )
    expect(
        extruded["mesh_revision"] > mesh_revision,
        f"cycle {index} extrusion did not advance the mesh revision",
    )

    host.call("modifier.add", {"object": oid, "type": "BEVEL", "name": "Bev"})
    host.call("modifier.set", {"object": oid, "modifier": "Bev", "properties": {"width": 0.02 + index % 5 * 0.01}})
    host.call("object.transform", {"object": oid, "location": [float(index % 7), 0.0, 0.0]})

    validation = host.result("mesh.validate", {"object": oid})
    expect(validation["manifold"] is True, f"cycle {index} produced non-manifold geometry")

    rolled = host.result("transaction.rollback", {"transaction": tx_id})
    expect(rolled["rolled_back"] is True, f"cycle {index} rollback did not report success")
    expect(
        rolled["fingerprint"] == baseline_fingerprint,
        f"cycle {index} rollback fingerprint {rolled['fingerprint']} != baseline",
    )

    revision_after = host.call("scene.describe")["revision"]
    expect(revision_after > revision_before, f"cycle {index} did not advance the scene revision")


def main() -> None:
    clean_scene()
    host = Host("soak")

    # A bystander that exists before every cycle and must survive all of them.
    bystander = host.result("object.create", {"kind": "cube", "name": "SoakBystander", "location": [9.0, 0.0, 0.0]})
    bystander_id = bystander["id"]
    bystander_state = host.result("object.inspect", {"object": bystander_id, "level": "deep"})
    baseline = host.fingerprint()

    object_datablocks = len(bpy.data.objects)
    mesh_datablocks = len(bpy.data.meshes)
    durations: list[float] = []

    for index in range(CYCLES):
        started = time.perf_counter()
        one_cycle(host, index, baseline)
        durations.append((time.perf_counter() - started) * 1000.0)

        expect(host.fingerprint() == baseline, f"cycle {index} left the scene fingerprint changed")
        expect(
            len(bpy.data.objects) == object_datablocks,
            f"cycle {index} leaked object datablocks: {len(bpy.data.objects)} != {object_datablocks}",
        )
        expect(
            len(bpy.data.meshes) <= mesh_datablocks,
            f"cycle {index} leaked mesh datablocks: {len(bpy.data.meshes)} > {mesh_datablocks}",
        )
        described = host.result("scene.describe")
        ids = [entry["id"] for entry in described["objects"]]
        expect(len(ids) == len(set(ids)), f"cycle {index} produced duplicate identities: {ids}")
        expect(described["identity_repairs"] == [], f"cycle {index} required identity repair: {described}")

    expect(
        host.result("object.inspect", {"object": bystander_id, "level": "deep"}) == bystander_state,
        "the bystander object changed across the soak",
    )

    summary = {
        "cycles": CYCLES,
        "blender": bpy.app.version_string,
        "baseline_fingerprint": baseline,
        "cycle_ms_mean": round(statistics.fmean(durations), 2),
        "cycle_ms_median": round(statistics.median(durations), 2),
        "cycle_ms_max": round(max(durations), 2),
    }
    (artifact_dir("blender-gate1-soak") / "summary.json").write_text(
        __import__("json").dumps(summary, indent=2), encoding="utf-8"
    )
    print(
        f"  {CYCLES} cycles  mean {summary['cycle_ms_mean']}ms  "
        f"median {summary['cycle_ms_median']}ms  max {summary['cycle_ms_max']}ms",
        flush=True,
    )


run_gate("GATE1_SOAK", main, "blender-gate1-soak")
