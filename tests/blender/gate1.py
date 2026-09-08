from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback

import bpy


ROOT = Path(__file__).resolve().parents[2]
HOST_ROOT = ROOT / "hosts" / "blender"
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))

from robovision_blender.ops import register_all  # noqa: E402
from robovision_blender.runtime import PROTOCOL_VERSION, RoboVisionRuntime  # noqa: E402


ARTIFACT_DIR = ROOT / "artifacts" / "blender-gate1"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
TRACE_PATH = ARTIFACT_DIR / "trace.json"
TRACE_CALLS: list[dict] = []


class GateFailure(AssertionError):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def clean_scene() -> None:
    # Establish the test baseline before RoboVision observes the scene. Direct
    # datablock removal avoids depending on selection/context operators here.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def main() -> None:
    clean_scene()

    runtime = RoboVisionRuntime()
    register_all(runtime.registry)
    runtime._registered_tools = True
    runtime._refresh_dirty_state()

    serial = 0

    def call(method: str, params: dict | None = None, *, if_revision: int | None = None, ok: bool = True) -> dict:
        nonlocal serial
        serial += 1
        request = {
            "rv": PROTOCOL_VERSION,
            "id": f"gate1-{serial}",
            "method": method,
            "params": params or {},
        }
        if if_revision is not None:
            request["if_revision"] = if_revision
        response = runtime.dispatch(request)
        TRACE_CALLS.append({"request": request, "response": response})
        if ok:
            expect(response.get("ok") is True, f"{method} failed: {response}")
        else:
            expect(response.get("ok") is False, f"{method} unexpectedly succeeded: {response}")
        return response

    hello = call("system.hello")
    methods = {entry["name"] for entry in hello["result"]["capabilities"]}
    required = {
        "system.method",
        "scene.snapshot",
        "scene.diff",
        "object.create",
        "object.transform",
        "mesh.inspect",
        "mesh.query",
        "mesh.validate",
        "mesh.extrude_faces",
        "modifier.add",
        "modifier.apply",
        "viewport.capture",
        "transaction.begin",
        "transaction.rollback",
    }
    expect(required.issubset(methods), f"missing Gate 1 methods: {sorted(required - methods)}")
    expect(hello["result"]["transport"]["python_worker_threads"] is False, "Blender host must not advertise worker-thread bpy execution")
    expect(hello["result"]["discovery"]["schemas_on_demand"] is True, "host must advertise schema discovery")

    method_doc = call("system.method", {"method": "mesh.query"})
    expect(method_doc["result"]["params_schema"]["type"] == "object", "mesh.query did not publish a parameter schema")
    expect("query" in method_doc["result"]["tags"], "mesh.query discovery tags are incomplete")

    baseline = call("scene.snapshot", {"deep": True})
    baseline_fp = baseline["result"]["fingerprint"]
    baseline_snapshot_id = baseline["result"]["snapshot"]
    baseline_revision = baseline["revision"]

    tx = call("transaction.begin", {"label": "Blender Gate 1"})
    tx_id = tx["result"]["transaction"]

    created = call(
        "object.create",
        {"kind": "cube", "name": "RV_GateCube", "size": 2.0, "location": [0.0, 0.0, 0.0]},
        if_revision=runtime.revision,
    )
    object_ref = created["result"]["id"]
    expect(object_ref.startswith("b3d:"), "created object did not receive a RoboVision id")

    mesh = call("mesh.inspect", {"object": object_ref, "evaluated": True})
    mesh_revision = mesh["result"]["mesh_revision"]
    expect(mesh["result"]["vertices"] == 8, "cube source mesh should have 8 vertices")
    expect(mesh["result"]["evaluated"]["faces"] == 6, "cube evaluated mesh should have 6 faces")

    validation = call("mesh.validate", {"object": object_ref})
    expect(validation["result"]["valid"] is True, "new cube failed mesh validation")
    expect(validation["result"]["manifold"] is True, "new cube must be manifold")

    # Never guess a raw cube face index. Select the actual +Z face semantically
    # and use the returned mesh revision as the topology handle for mutation.
    top_face = call(
        "mesh.query",
        {
            "object": object_ref,
            "domain": "FACE",
            "space": "OBJECT",
            "normal": {"direction": [0.0, 0.0, 1.0], "min_dot": 0.999},
            "sort_by": "-z",
        },
    )["result"]
    expect(top_face["count"] == 1, f"expected one +Z cube face: {top_face}")
    expect(top_face["mesh_revision"] == mesh_revision, "semantic query returned an unexpected mesh revision")

    extruded = call(
        "mesh.extrude_faces",
        {
            "object": object_ref,
            "expected_mesh_revision": top_face["mesh_revision"],
            "face_indices": top_face["indices"],
            "translation": [0.0, 0.0, 0.5],
        },
        if_revision=runtime.revision,
    )
    extruded_revision = extruded["result"]["mesh_revision"]
    expect(extruded_revision == mesh_revision + 1, "topology revision did not advance after extrusion")

    added_modifier = call(
        "modifier.add",
        {
            "object": object_ref,
            "type": "BEVEL",
            "name": "RV_GateBevel",
            "properties": {"width": 0.04, "segments": 2},
        },
        if_revision=runtime.revision,
    )
    expect(added_modifier["result"]["modifier"]["name"] == "RV_GateBevel", "modifier was not created")

    applied_modifier = call(
        "modifier.apply",
        {"object": object_ref, "modifier": "RV_GateBevel"},
        if_revision=runtime.revision,
    )
    expect(applied_modifier["result"]["mesh_revision"] == extruded_revision + 1, "applying topology modifier did not advance mesh revision")

    post_edit_validation = call("mesh.validate", {"object": object_ref})
    expect(post_edit_validation["result"]["valid"] is True, f"edited mesh failed validation: {post_edit_validation['result']['issues']}")
    expect(post_edit_validation["result"]["manifold"] is True, f"edited mesh is non-manifold: {post_edit_validation['result']['issues']}")
    expect(post_edit_validation["result"]["counts"]["vertices"] > 8, "topology edits did not produce additional vertices")

    stale = call(
        "mesh.bevel",
        {
            "object": object_ref,
            "expected_mesh_revision": mesh_revision,
            "edge_indices": [0],
            "width": 0.05,
        },
        if_revision=runtime.revision,
        ok=False,
    )
    expect(stale["error"]["code"] == "STALE_TOPOLOGY", f"expected STALE_TOPOLOGY, got {stale}")

    before_transform_revision = runtime.revision
    moved = call(
        "object.transform",
        {"object": object_ref, "location": [1.25, -0.5, 0.75]},
        if_revision=before_transform_revision,
    )
    expect(moved["result"]["matrix_world"][0][3] == 1.25, "object transform was not applied")

    stale_scene = call(
        "object.transform",
        {"object": object_ref, "location": [9.0, 9.0, 9.0]},
        if_revision=before_transform_revision,
        ok=False,
    )
    expect(stale_scene["error"]["code"] == "STALE_REVISION", f"expected STALE_REVISION, got {stale_scene}")

    changed = call("scene.diff", {"from_snapshot": baseline_snapshot_id, "deep": True})
    expect(changed["result"]["equal"] is False, "scene diff failed to observe mutation")
    expect(any(entry["id"] == object_ref for entry in changed["result"]["created"]), "scene diff did not report created object")

    # Gate 1 requires actual visual evidence, not only machine-readable state.
    capture_path = ARTIFACT_DIR / "viewport.png"
    call("viewport.focus", {"object": object_ref, "padding": 1.35})
    capture = call(
        "viewport.capture",
        {"path": str(capture_path), "shading": "SOLID", "overlays": False},
    )
    expect(capture_path.is_file(), "viewport artifact was not written")
    expect(capture_path.stat().st_size > 1024, "viewport artifact is suspiciously small")
    view = capture["result"]["view"]
    expect(view["viewport"]["width"] > 0 and view["viewport"]["height"] > 0, "viewport provenance is missing dimensions")
    expect(len(view["view_matrix"]) == 4 and len(view["perspective_matrix"]) == 4, "viewport provenance is missing matrices")

    rolled = call("transaction.rollback", {"transaction": tx_id, "max_steps": 64})
    expect(rolled["result"]["rolled_back"] is True, "transaction did not report rollback")
    expect(rolled["result"]["fingerprint"] == baseline_fp, "rollback result fingerprint differs from baseline")

    restored = call("scene.snapshot", {"deep": True})
    expect(restored["result"]["fingerprint"] == baseline_fp, "post-rollback scene fingerprint differs from baseline")
    expect(restored["result"]["state"]["objects"] == [], "post-rollback scene still contains objects")
    expect(restored["revision"] > baseline_revision, "scene revision did not advance across Gate 1")

    TRACE_PATH.write_text(
        json.dumps(
            {
                "blender": bpy.app.version_string,
                "protocol": PROTOCOL_VERSION,
                "baseline_fingerprint": baseline_fp,
                "final_fingerprint": restored["result"]["fingerprint"],
                "calls": TRACE_CALLS,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("ROBOVISION_GATE1_PASS", baseline_fp, "calls=", len(TRACE_CALLS))


try:
    main()
except Exception:
    traceback.print_exc()
    try:
        TRACE_PATH.write_text(
            json.dumps({"error": traceback.format_exc(), "calls": TRACE_CALLS}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    raise
else:
    # Interactive Blender is used so Undo and VIEW_3D context are real. Exit
    # explicitly once the gate has completed.
    bpy.ops.wm.quit_blender()
