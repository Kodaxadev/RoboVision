from __future__ import annotations

from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
HOST_ROOT = ROOT / "hosts" / "blender"
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))

from robovision_blender.ops import register_all  # noqa: E402
from robovision_blender.runtime import PROTOCOL_VERSION, RoboVisionRuntime  # noqa: E402


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)

    runtime = RoboVisionRuntime()
    register_all(runtime.registry)
    runtime._registered_tools = True
    runtime._refresh_dirty_state()
    serial = 0

    def call(method: str, params: dict | None = None, *, ok: bool = True):
        nonlocal serial
        serial += 1
        response = runtime.dispatch(
            {
                "rv": PROTOCOL_VERSION,
                "id": f"semantic-{serial}",
                "method": method,
                "params": params or {},
                **({"if_revision": runtime.revision} if method in {"object.create", "object.transform", "mesh.extrude_faces"} else {}),
            }
        )
        expect(response.get("ok") is ok, f"{method}: {response}")
        return response

    cube = call("object.create", {"kind": "cube", "name": "QueryCube", "size": 2.0})["result"]
    cube_id = cube["id"]

    top = call(
        "mesh.query",
        {
            "object": cube_id,
            "domain": "FACE",
            "normal": {"direction": [0, 0, 1], "min_dot": 0.999},
            "sort_by": "-z",
        },
    )["result"]
    expect(top["count"] == 1, f"expected one +Z cube face, got {top}")
    expect(top["items"][0]["normal_dot"] > 0.999, "top-face query returned wrong normal")

    manifold_edges = call(
        "mesh.query",
        {"object": cube_id, "domain": "EDGE", "manifold": True, "boundary": False, "details": False},
    )["result"]
    expect(manifold_edges["count"] == 12, f"cube should have 12 manifold non-boundary edges: {manifold_edges}")

    extruded = call(
        "mesh.extrude_faces",
        {
            "object": cube_id,
            "expected_mesh_revision": top["mesh_revision"],
            "face_indices": top["indices"],
            "translation": [0, 0, 0.5],
        },
    )["result"]
    expect(extruded["mesh_revision"] == top["mesh_revision"] + 1, "semantic selection was not usable as a revision-scoped mutation handle")

    # Build two islands to prove connectivity analysis does not confuse spatial
    # proximity with topological connectivity.
    islands = call(
        "object.create",
        {
            "kind": "mesh",
            "name": "TwoIslands",
            "vertices": [
                [-2, 0, 0], [-1, 0, 0], [-1.5, 1, 0],
                [1, 0, 0], [2, 0, 0], [1.5, 1, 0],
            ],
            "faces": [[0, 1, 2], [3, 4, 5]],
        },
    )["result"]
    components = call("mesh.components", {"object": islands["id"], "space": "OBJECT"})["result"]
    expect(components["component_count"] == 2, f"expected two disconnected components: {components}")
    expect(all(component["counts"]["vertices"] == 3 for component in components["components"]), "component vertex counts are wrong")

    right_faces = call(
        "mesh.query",
        {
            "object": islands["id"],
            "domain": "FACE",
            "bounds": {"min": [0, -10, -10], "max": [10, 10, 10]},
            "space": "OBJECT",
        },
    )["result"]
    expect(right_faces["count"] == 1, f"bounds query should isolate right island face: {right_faces}")

    print("ROBOVISION_SEMANTIC_QUERY_PASS")


main()
