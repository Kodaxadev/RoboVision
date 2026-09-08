from __future__ import annotations

import hashlib
import json
from typing import Any

import bpy

from .identity import mesh_revision, normalize_object_ids, object_id


def _rounded(value: float) -> float:
    return round(float(value), 9)


def _matrix(matrix) -> list[list[float]]:
    return [[_rounded(v) for v in row] for row in matrix]


def _mesh_digest(mesh: bpy.types.Mesh) -> str:
    """Hash source geometry, material slots and polygon material assignment."""
    h = hashlib.sha256()
    for vertex in mesh.vertices:
        h.update(f"v:{vertex.index}:{_rounded(vertex.co.x)}:{_rounded(vertex.co.y)}:{_rounded(vertex.co.z)};".encode())
    for edge in mesh.edges:
        h.update(f"e:{edge.vertices[0]}:{edge.vertices[1]};".encode())
    for poly in mesh.polygons:
        h.update((f"p:{poly.index}:" + ",".join(str(v) for v in poly.vertices) + f":m{poly.material_index};").encode())
    for slot in mesh.materials:
        h.update(f"mat:{slot.name_full if slot else '<none>'};".encode())
    return h.hexdigest()


def object_snapshot(obj: bpy.types.Object, *, deep: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": object_id(obj),
        "name": obj.name_full,
        "type": obj.type,
        "matrix_world": _matrix(obj.matrix_world),
        "parent": object_id(obj.parent) if obj.parent else None,
        "hidden_viewport": bool(obj.hide_viewport),
        "hidden_render": bool(obj.hide_render),
        "library": obj.library.filepath if obj.library else None,
    }
    if obj.type == "MESH" and obj.data is not None:
        mesh = obj.data
        data["mesh"] = {
            "revision": mesh_revision(obj),
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
            "digest": _mesh_digest(mesh) if deep else None,
        }
    data["modifiers"] = [
        {"name": modifier.name, "type": modifier.type, "show_viewport": bool(modifier.show_viewport), "show_render": bool(modifier.show_render)}
        for modifier in obj.modifiers
    ]
    return data


def scene_snapshot(*, deep: bool = True) -> dict[str, Any]:
    repaired = normalize_object_ids()
    objects = [object_snapshot(obj, deep=deep) for obj in sorted(bpy.context.scene.objects, key=lambda item: object_id(item))]
    body = {
        "scene": bpy.context.scene.name_full,
        "frame": int(bpy.context.scene.frame_current),
        "objects": objects,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {"fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(), "state": body, "identity_repairs": repaired}


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_map = {obj["id"]: obj for obj in before["state"]["objects"]}
    after_map = {obj["id"]: obj for obj in after["state"]["objects"]}
    created = sorted(set(after_map) - set(before_map))
    deleted = sorted(set(before_map) - set(after_map))
    changed = sorted(oid for oid in set(before_map) & set(after_map) if before_map[oid] != after_map[oid])
    return {
        "before_fingerprint": before["fingerprint"],
        "after_fingerprint": after["fingerprint"],
        "created": [{"id": oid, "name": after_map[oid]["name"]} for oid in created],
        "deleted": [{"id": oid, "name": before_map[oid]["name"]} for oid in deleted],
        "changed": [{"id": oid, "before": before_map[oid], "after": after_map[oid]} for oid in changed],
        "equal": before["fingerprint"] == after["fingerprint"],
    }
