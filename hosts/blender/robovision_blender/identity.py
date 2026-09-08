from __future__ import annotations

import hashlib
import uuid
from typing import Any

import bpy

from .registry import HostError

OBJECT_ID_KEY = "_robovision_id"
MESH_REV_KEY = "_robovision_mesh_revision"


def _derived_linked_id(obj: bpy.types.Object) -> str:
    library = obj.library.filepath if obj.library else ""
    raw = f"{library}|{obj.name_full}".encode("utf-8")
    return "b3d:linked:" + hashlib.sha256(raw).hexdigest()[:32]


def object_id(obj: bpy.types.Object, *, create: bool = True) -> str:
    existing = obj.get(OBJECT_ID_KEY)
    if isinstance(existing, str) and existing:
        return existing
    if not create:
        return _derived_linked_id(obj) if obj.library else ""
    if obj.library is not None:
        return _derived_linked_id(obj)
    value = "b3d:" + str(uuid.uuid4())
    try:
        obj[OBJECT_ID_KEY] = value
    except (TypeError, AttributeError, RuntimeError):
        return _derived_linked_id(obj)
    return value


def resolve_object(ref: Any) -> bpy.types.Object:
    if isinstance(ref, bpy.types.Object):
        return ref
    if not isinstance(ref, str) or not ref:
        raise HostError("INVALID_PARAMS", "object must be a RoboVision id or object name")
    # IDs are authoritative. Names are accepted only as an explicit convenience fallback.
    for obj in bpy.data.objects:
        if object_id(obj) == ref:
            return obj
    obj = bpy.data.objects.get(ref)
    if obj is None:
        raise HostError("NOT_FOUND", f"object not found: {ref}")
    return obj


def normalize_object_ids() -> list[dict[str, str]]:
    """Repair copied custom IDs on local objects and report every reassignment."""
    seen: dict[str, bpy.types.Object] = {}
    repaired: list[dict[str, str]] = []
    for obj in sorted(bpy.data.objects, key=lambda item: item.name_full):
        oid = object_id(obj)
        if obj.library is not None:
            continue
        prior = seen.get(oid)
        if prior is None:
            seen[oid] = obj
            continue
        new_id = "b3d:" + str(uuid.uuid4())
        obj[OBJECT_ID_KEY] = new_id
        repaired.append({"object": obj.name_full, "duplicate": oid, "assigned": new_id})
        seen[new_id] = obj
    return repaired


def mesh_revision(obj: bpy.types.Object) -> int:
    if obj.type != "MESH" or obj.data is None:
        raise HostError("INVALID_PARAMS", "object is not a mesh")
    value = obj.data.get(MESH_REV_KEY, 0)
    return int(value) if isinstance(value, (int, float)) else 0


def assert_mesh_revision(obj: bpy.types.Object, expected: Any) -> int:
    current = mesh_revision(obj)
    if expected is None:
        raise HostError("INVALID_PARAMS", "expected_mesh_revision is required for topology-indexed mutation")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise HostError("INVALID_PARAMS", "expected_mesh_revision must be an integer")
    if current != expected:
        raise HostError(
            "STALE_TOPOLOGY",
            "mesh topology revision changed",
            data={"expected": expected, "actual": current, "object": object_id(obj)},
            retryable=True,
        )
    return current


def bump_mesh_revision(obj: bpy.types.Object) -> int:
    current = mesh_revision(obj)
    try:
        obj.data[MESH_REV_KEY] = current + 1
    except (TypeError, AttributeError, RuntimeError) as exc:
        raise HostError("UNSUPPORTED", "mesh data is not writable", data={"object": object_id(obj)}) from exc
    return current + 1
