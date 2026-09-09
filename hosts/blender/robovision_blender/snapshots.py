from __future__ import annotations

from array import array
import hashlib
import json
from typing import Any

import bpy

from .identity import mesh_revision, normalize_object_ids, object_id, topology_signature

# Snapshot levels are layered so callers can pay only for the evidence they
# need. Verified transactions always use "deep"; cheap listings can stay
# "shallow". Anything that claims restoration must compare deep fingerprints.
SHALLOW = "shallow"
STANDARD = "standard"
DEEP = "deep"
LEVELS = (SHALLOW, STANDARD, DEEP)
_ORDER = {name: index for index, name in enumerate(LEVELS)}

# Custom properties RoboVision owns. They are bookkeeping, not authored state,
# and including them would make a fingerprint depend on its own side effects.
_INTERNAL_KEYS = {"_robovision_id", "_robovision_mesh_revision", "_robovision_topology"}


def resolve_level(params: dict[str, Any] | None = None, *, deep: Any = None, default: str = DEEP) -> str:
    """Accept either the layered `level` parameter or the older `deep` boolean."""
    if params:
        raw = params.get("level")
        if raw is not None:
            level = str(raw).lower()
            if level not in _ORDER:
                from .registry import HostError

                raise HostError("INVALID_PARAMS", f"level must be one of {', '.join(LEVELS)}")
            return level
        if "deep" in params:
            deep = params.get("deep")
    if deep is None:
        return default
    return DEEP if bool(deep) else STANDARD


def _at_least(level: str, minimum: str) -> bool:
    return _ORDER[level] >= _ORDER[minimum]


def _rounded(value: float) -> float:
    return round(float(value), 9)


def _matrix(matrix) -> list[list[float]]:
    return [[_rounded(v) for v in row] for row in matrix]


def _jsonable(value: Any) -> Any:
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        return _rounded(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, bpy.types.ID):
        return value.name_full
    if hasattr(value, "__len__") and not isinstance(value, str):
        try:
            return [_jsonable(item) for item in value]
        except TypeError:
            return str(value)
    if hasattr(value, "items"):
        try:
            return {str(key): _jsonable(item) for key, item in value.items()}
        except TypeError:
            return str(value)
    return str(value)


def _custom_properties(source) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        keys = list(source.keys())
    except (AttributeError, TypeError):
        return result
    for key in sorted(keys):
        if key in _INTERNAL_KEYS:
            continue
        try:
            result[key] = _jsonable(source[key])
        except (KeyError, TypeError, ValueError):
            result[key] = "<unreadable>"
    return result


def _modifier_state(modifier) -> dict[str, Any]:
    """Capture the authored settings of one modifier.

    Recording only name/type/visibility would let `modifier.set` change a value
    without moving the fingerprint, and a rollback could then claim restoration
    while the modifier still held the new value.
    """
    settings: dict[str, Any] = {}
    for prop in modifier.bl_rna.properties:
        identifier = prop.identifier
        if identifier == "rna_type" or prop.is_readonly or prop.type == "COLLECTION":
            continue
        try:
            value = getattr(modifier, identifier)
        except (AttributeError, RuntimeError):
            continue
        settings[identifier] = _jsonable(value)
    return {"name": modifier.name, "type": modifier.type, "settings": settings}


def _mesh_geometry_digest(mesh: bpy.types.Mesh) -> str:
    """Hash positions and material assignment on top of the topology signature."""
    digest = hashlib.sha256()
    digest.update(topology_signature(mesh).encode())

    vertex_count = len(mesh.vertices)
    if vertex_count:
        coordinates = array("f", bytes(vertex_count * 3 * 4))
        mesh.vertices.foreach_get("co", coordinates)
        digest.update(coordinates.tobytes())
    poly_count = len(mesh.polygons)
    if poly_count:
        material_indices = array("i", bytes(poly_count * 4))
        mesh.polygons.foreach_get("material_index", material_indices)
        digest.update(material_indices.tobytes())
    for material in mesh.materials:
        digest.update(f"mat:{material.name_full if material else '<none>'};".encode())
    return digest.hexdigest()


def _edit_mode(obj: bpy.types.Object) -> bool:
    """Report whether the datablock may lag behind a live Edit Mode session.

    Snapshots deliberately do not call `update_from_editmode()`. Publishing the
    edit BMesh would make taking a fingerprint change the very state the
    fingerprint claims to measure, which would break transaction proofs. Read
    operations sync explicitly instead; a snapshot only records that geometry is
    being edited so no caller mistakes a stale digest for current truth.
    """
    return obj.mode == "EDIT"


def object_snapshot(obj: bpy.types.Object, *, deep: bool | None = None, level: str | None = None) -> dict[str, Any]:
    resolved = level if level in _ORDER else resolve_level(deep=deep)
    edit_mode = _edit_mode(obj)
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
    data["mode"] = obj.mode
    if edit_mode:
        # The digest below describes obj.data, which Blender only refreshes on
        # mode exit, so mark the geometry evidence as provisional.
        data["geometry_pending_editmode"] = True

    if _at_least(resolved, STANDARD):
        data["parent_type"] = obj.parent_type
        data["matrix_parent_inverse"] = _matrix(obj.matrix_parent_inverse)
        data["data"] = obj.data.name_full if obj.data is not None else None
        data["collections"] = sorted(collection.name_full for collection in obj.users_collection)
        data["material_slots"] = [
            {"link": slot.link, "material": slot.material.name_full if slot.material else None}
            for slot in obj.material_slots
        ]
        data["custom_properties"] = _custom_properties(obj)
        try:
            data["hidden_in_view_layer"] = bool(obj.hide_get())
        except RuntimeError:
            data["hidden_in_view_layer"] = None
        data["modifiers"] = [_modifier_state(modifier) for modifier in obj.modifiers]
    else:
        data["modifiers"] = [
            {"name": modifier.name, "type": modifier.type}
            for modifier in obj.modifiers
        ]

    if obj.type == "MESH" and obj.data is not None:
        mesh = obj.data
        mesh_state: dict[str, Any] = {
            "revision": mesh_revision(obj),
            "vertices": len(mesh.vertices),
            "edges": len(mesh.edges),
            "faces": len(mesh.polygons),
        }
        if _at_least(resolved, STANDARD):
            mesh_state["topology"] = topology_signature(mesh)
        if _at_least(resolved, DEEP):
            mesh_state["digest"] = _mesh_geometry_digest(mesh)
        data["mesh"] = mesh_state
    return data


def scene_snapshot(*, deep: bool | None = None, level: str | None = None) -> dict[str, Any]:
    resolved = level if level in _ORDER else resolve_level(deep=deep)
    repaired = normalize_object_ids()
    scene = bpy.context.scene
    objects = [
        object_snapshot(obj, level=resolved)
        for obj in sorted(scene.objects, key=lambda item: object_id(item))
    ]
    body: dict[str, Any] = {
        "scene": scene.name_full,
        "frame": int(scene.frame_current),
        "objects": objects,
    }
    if _at_least(resolved, STANDARD):
        # Objects that exist in the file but are not linked to the scene stay
        # out of `objects`; recording them keeps a rollback from proving
        # restoration while an orphaned datablock survives.
        body["unlinked_objects"] = sorted(
            object_id(obj) for obj in bpy.data.objects if obj.name not in scene.objects
        )
        body["collections"] = sorted(collection.name_full for collection in bpy.data.collections)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {
        "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "level": resolved,
        "state": body,
        "identity_repairs": repaired,
    }


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_map = {obj["id"]: obj for obj in before["state"]["objects"]}
    after_map = {obj["id"]: obj for obj in after["state"]["objects"]}
    created = sorted(set(after_map) - set(before_map))
    deleted = sorted(set(before_map) - set(after_map))
    changed = sorted(oid for oid in set(before_map) & set(after_map) if before_map[oid] != after_map[oid])
    return {
        "before_fingerprint": before["fingerprint"],
        "after_fingerprint": after["fingerprint"],
        "before_level": before.get("level"),
        "after_level": after.get("level"),
        "comparable": before.get("level") == after.get("level"),
        "created": [{"id": oid, "name": after_map[oid]["name"]} for oid in created],
        "deleted": [{"id": oid, "name": before_map[oid]["name"]} for oid in deleted],
        "changed": [{"id": oid, "before": before_map[oid], "after": after_map[oid]} for oid in changed],
        "equal": before["fingerprint"] == after["fingerprint"],
    }
