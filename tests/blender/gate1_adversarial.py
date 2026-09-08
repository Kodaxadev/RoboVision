"""Gate 1 negative paths: what RoboVision must refuse, and what it must repair.

The happy path is covered by gate1.py. This gate exercises the failures that
decide whether an agent can trust the host: partially applied mutations, state
that moved under the agent, topology indices that no longer mean anything,
identity that could silently migrate to a copy, and edits made by a human while
a transaction is open.

Every scenario asserts on observable state, not just on the response code.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402


def scenario_partial_mutation_recovers() -> None:
    """A mutation that fails halfway must leave no partial state behind.

    `modifier.set` applies properties in sequence, so a request whose second
    property is invalid genuinely half-applies before raising. That is the real
    shape of the hazard, not a simulated one.
    """
    clean_scene()
    host = Host("recovery")
    created = host.result("object.create", {"kind": "cube", "name": "Recover"})
    oid = created["id"]
    host.call("modifier.add", {"object": oid, "type": "BEVEL", "name": "Bev"})

    modifier = bpy.data.objects["Recover"].modifiers["Bev"]
    original_width = modifier.width
    original_segments = modifier.segments
    before = host.fingerprint()

    response = host.call(
        "modifier.set",
        {
            "object": oid,
            "modifier": "Bev",
            # width applies, then the enum rejects the value: a true mid-operation failure.
            "properties": {"width": 0.42, "limit_method": "NOT_A_REAL_ENUM_VALUE"},
        },
        ok=False,
    )

    recovery = response["error"].get("data", {}).get("automatic_recovery")
    expect(recovery is not None, f"failed mutation did not report automatic recovery: {response}")
    expect(recovery.get("recovered") is True, f"recovery did not succeed: {recovery}")

    modifier = bpy.data.objects["Recover"].modifiers["Bev"]
    expect(
        abs(modifier.width - original_width) < 1e-9,
        f"partial mutation survived recovery: width {modifier.width} != {original_width}",
    )
    expect(modifier.segments == original_segments, "recovery changed an unrelated property")
    expect(host.fingerprint() == before, "post-recovery fingerprint differs from pre-operation fingerprint")


def scenario_stale_revision_rejected() -> None:
    """A mutation planned against an observed revision must not apply after drift."""
    clean_scene()
    host = Host("stale-rev")
    created = host.result("object.create", {"kind": "cube", "name": "Drift"})
    oid = created["id"]
    observed_revision = host.call("scene.describe")["revision"]

    # A human moves the object in the editor while the agent is still thinking.
    bpy.data.objects["Drift"].location.x = 3.0
    bpy.context.view_layer.update()

    host.call(
        "object.transform",
        {"object": oid, "location": [0.0, 5.0, 0.0]},
        if_revision=observed_revision,
        ok=False,
        code="STALE_REVISION",
    )
    expect(
        abs(bpy.data.objects["Drift"].location.x - 3.0) < 1e-9,
        "rejected mutation still moved the object",
    )
    expect(
        abs(bpy.data.objects["Drift"].location.y) < 1e-9,
        "rejected mutation applied its own translation",
    )

    # Re-observing must make the same mutation acceptable again.
    current = host.call("scene.describe")["revision"]
    host.call("object.transform", {"object": oid, "location": [0.0, 5.0, 0.0]}, if_revision=current)
    expect(abs(bpy.data.objects["Drift"].location.y - 5.0) < 1e-9, "re-observed mutation did not apply")


def scenario_stale_topology_rejected() -> None:
    """Indices are only meaningful for the revision that produced them."""
    clean_scene()
    host = Host("stale-topo")
    created = host.result("object.create", {"kind": "cube", "name": "Topo"})
    oid = created["id"]
    observed = host.result("mesh.inspect", {"object": oid})["mesh_revision"]

    target = bpy.data.objects["Topo"]
    bm = bmesh.new()
    bm.from_mesh(target.data)
    bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=1, use_grid_fill=True)
    bm.to_mesh(target.data)
    bm.free()
    target.data.update()

    faces_after_external_edit = len(target.data.polygons)
    expect(faces_after_external_edit > 6, "external topology edit did not take effect")

    host.call(
        "mesh.extrude_faces",
        {"object": oid, "face_indices": [0], "translation": [0.0, 0.0, 1.0], "expected_mesh_revision": observed},
        ok=False,
        code="STALE_TOPOLOGY",
    )
    expect(
        len(bpy.data.objects["Topo"].data.polygons) == faces_after_external_edit,
        "rejected topology mutation still changed the mesh",
    )

    # The host must publish a revision that reflects the external edit.
    refreshed = host.result("mesh.inspect", {"object": oid})["mesh_revision"]
    expect(refreshed != observed, "mesh revision did not advance after an out-of-band topology change")
    host.call(
        "mesh.extrude_faces",
        {"object": oid, "face_indices": [0], "translation": [0.0, 0.0, 1.0], "expected_mesh_revision": refreshed},
    )


def scenario_indexed_mutation_requires_revision() -> None:
    """No topology-indexed mutation may run without an explicit precondition."""
    clean_scene()
    host = Host("needs-rev")
    oid = host.result("object.create", {"kind": "cube", "name": "Needs"})["id"]
    indexed = [
        ("mesh.extrude_faces", {"face_indices": [0], "translation": [0.0, 0.0, 0.1]}),
        ("mesh.bevel", {"edge_indices": [0], "width": 0.01}),
        ("mesh.subdivide_edges", {"edge_indices": [0]}),
        ("mesh.inset_faces", {"face_indices": [0], "thickness": 0.05}),
        ("mesh.triangulate", {"face_indices": [0]}),
        ("mesh.recalc_normals", {"face_indices": [0]}),
        ("mesh.solidify", {"face_indices": [0], "thickness": 0.05}),
    ]
    for method, params in indexed:
        host.call(method, {"object": oid, **params}, ok=False, code="INVALID_PARAMS")


def scenario_identity_survives_duplication() -> None:
    """Identity must never migrate to a copy, whichever way the copy is made."""
    clean_scene()
    host = Host("identity")
    original = host.result("object.create", {"kind": "cube", "name": "Zeta"})
    original_id = original["id"]

    # 1. Raw datablock copy named so it sorts before the original.
    source = bpy.data.objects["Zeta"]
    manual = source.copy()
    manual.data = source.data.copy()
    manual.name = "Alpha"
    bpy.context.scene.collection.objects.link(manual)

    # 2. Blender's own duplicate operator.
    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    bpy.ops.object.duplicate(linked=False)

    # 3. RoboVision's structured duplication.
    host.call("object.duplicate", {"object": original_id, "name": "Aardvark"})

    described = host.result("scene.describe")
    ids = [entry["id"] for entry in described["objects"]]
    expect(len(ids) == len(set(ids)), f"duplicate RoboVision ids survived normalization: {ids}")
    expect(len(ids) == 4, f"expected four objects after three duplications, got {len(ids)}")

    by_name = {entry["name"]: entry["id"] for entry in described["objects"]}
    expect(
        by_name.get("Zeta") == original_id,
        f"the original object lost its identity to a copy: {by_name}",
    )

    # The id the agent is holding must still address the original object.
    resolved = host.result("object.inspect", {"object": original_id})
    expect(resolved["name"] == "Zeta", f"id {original_id} now resolves to {resolved['name']}")


def scenario_edit_mode_refused() -> None:
    """Writes through obj.data vanish on mode exit, so they must be refused."""
    clean_scene()
    host = Host("edit-mode")
    oid = host.result("object.create", {"kind": "cube", "name": "Edit"})["id"]
    target = bpy.data.objects["Edit"]
    bpy.context.view_layer.objects.active = target
    target.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        revision = host.result("mesh.inspect", {"object": oid})["mesh_revision"]
        host.call(
            "mesh.subdivide_edges",
            {"object": oid, "edge_indices": [0], "expected_mesh_revision": revision},
            ok=False,
            code="INVALID_CONTEXT",
        )
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    expect(len(bpy.data.objects["Edit"].data.polygons) == 6, "refused Edit Mode mutation still changed the mesh")


def scenario_rollback_restores_modifier_settings() -> None:
    """Regression: a fingerprint that ignores modifier settings proves nothing.

    Before modifier settings were part of the deep snapshot, this sequence
    returned `rolled_back: true, undo_steps: 0` with the new width still applied.
    """
    clean_scene()
    host = Host("modifier-rollback")
    oid = host.result("object.create", {"kind": "cube", "name": "Mod"})["id"]
    host.call("modifier.add", {"object": oid, "type": "BEVEL", "name": "Bev"})
    original_width = bpy.data.objects["Mod"].modifiers["Bev"].width

    tx = host.result("transaction.begin", {"label": "modifier settings"})["transaction"]
    host.call("modifier.set", {"object": oid, "modifier": "Bev", "properties": {"width": 0.5}})
    expect(abs(bpy.data.objects["Mod"].modifiers["Bev"].width - 0.5) < 1e-9, "modifier.set did not apply")

    rolled = host.result("transaction.rollback", {"transaction": tx})
    expect(rolled["rolled_back"] is True, f"rollback did not report success: {rolled}")
    restored = bpy.data.objects["Mod"].modifiers["Bev"].width
    expect(
        abs(restored - original_width) < 1e-9,
        f"rollback claimed restoration but width is {restored}, expected {original_width}",
    )


def scenario_transaction_refuses_to_bury_human_edits() -> None:
    """An out-of-band edit during a transaction must block automatic rollback."""
    clean_scene()
    host = Host("contamination")
    oid = host.result("object.create", {"kind": "cube", "name": "Shared"})["id"]
    tx = host.result("transaction.begin", {"label": "contaminated"})["transaction"]
    host.call("object.transform", {"object": oid, "location": [1.0, 0.0, 0.0]})

    # A human adds their own object while the agent's transaction is open.
    bystander = bpy.data.objects.new("HumanWork", None)
    bpy.context.scene.collection.objects.link(bystander)
    bpy.context.view_layer.update()
    host.call("scene.describe")  # let the host observe the external change

    host.call("transaction.rollback", {"transaction": tx}, ok=False, code="TRANSACTION_CONTAMINATED")
    expect("HumanWork" in bpy.data.objects, "refused rollback still removed the human's object")

    forced = host.result("transaction.rollback", {"transaction": tx, "force": True})
    expect(forced["forced_after_external_change"] is True, "forced rollback did not report contamination")


def scenario_unrelated_state_survives_rollback() -> None:
    """Rolling back one object's edits must not disturb the rest of the scene."""
    clean_scene()
    host = Host("bystander")
    bystander_id = host.result("object.create", {"kind": "cube", "name": "Bystander", "location": [7.0, 0.0, 0.0]})["id"]
    subject_id = host.result("object.create", {"kind": "cube", "name": "Subject"})["id"]
    bystander_before = host.result("object.inspect", {"object": bystander_id, "level": "deep"})
    before = host.fingerprint()

    tx = host.result("transaction.begin", {"label": "scoped"})["transaction"]
    revision = host.result("mesh.inspect", {"object": subject_id})["mesh_revision"]
    host.call(
        "mesh.extrude_faces",
        {"object": subject_id, "face_indices": [0], "translation": [0.0, 0.0, 2.0], "expected_mesh_revision": revision},
    )
    host.call("object.transform", {"object": subject_id, "location": [0.0, 0.0, 4.0]})
    rolled = host.result("transaction.rollback", {"transaction": tx})

    expect(rolled["rolled_back"] is True, f"rollback failed: {rolled}")
    expect(host.fingerprint() == before, "scene fingerprint did not return to the transaction baseline")
    bystander_after = host.result("object.inspect", {"object": bystander_id, "level": "deep"})
    expect(bystander_after == bystander_before, "rollback altered an object the transaction never touched")


def main() -> None:
    scenarios = (
        scenario_partial_mutation_recovers,
        scenario_stale_revision_rejected,
        scenario_stale_topology_rejected,
        scenario_indexed_mutation_requires_revision,
        scenario_identity_survives_duplication,
        scenario_edit_mode_refused,
        scenario_rollback_restores_modifier_settings,
        scenario_transaction_refuses_to_bury_human_edits,
        scenario_unrelated_state_survives_rollback,
    )
    for scenario in scenarios:
        scenario()
        print(f"  ok {scenario.__name__}", flush=True)
    (artifact_dir("blender-gate1-adversarial") / "scenarios.txt").write_text(
        "\n".join(scenario.__name__ for scenario in scenarios) + "\n", encoding="utf-8"
    )


run_gate("GATE1_ADVERSARIAL", main, "blender-gate1-adversarial")
