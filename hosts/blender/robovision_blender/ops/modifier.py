from __future__ import annotations

import bpy

from ..context import active_object_override
from ..identity import bump_mesh_revision, object_id, resolve_object
from ..registry import HostError


def _modifier(obj, name):
    if not isinstance(name, str) or not name:
        raise HostError("INVALID_PARAMS", "modifier name is required")
    modifier = obj.modifiers.get(name)
    if modifier is None:
        raise HostError("NOT_FOUND", f"modifier not found: {name}")
    return modifier


def _set_property(modifier, key: str, value) -> None:
    prop = modifier.bl_rna.properties.get(key)
    if prop is None:
        raise HostError("INVALID_PARAMS", f"unknown modifier property: {key}")
    if prop.is_readonly:
        raise HostError("INVALID_PARAMS", f"modifier property is read-only: {key}")
    try:
        setattr(modifier, key, value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise HostError("INVALID_PARAMS", f"invalid value for modifier property {key}: {exc}") from exc


def list_modifiers(params, _runtime):
    obj = resolve_object(params.get("object"))
    return {
        "object": object_id(obj),
        "modifiers": [
            {"name": modifier.name, "type": modifier.type, "show_viewport": bool(modifier.show_viewport), "show_render": bool(modifier.show_render)}
            for modifier in obj.modifiers
        ],
    }


def add(params, _runtime):
    obj = resolve_object(params.get("object"))
    modifier_type = str(params.get("type", "")).upper()
    if not modifier_type:
        raise HostError("INVALID_PARAMS", "modifier type is required")
    name = str(params.get("name") or modifier_type.title())
    try:
        modifier = obj.modifiers.new(name=name, type=modifier_type)
    except (TypeError, RuntimeError) as exc:
        raise HostError("INVALID_PARAMS", f"cannot create modifier {modifier_type}: {exc}") from exc
    properties = params.get("properties", {})
    if not isinstance(properties, dict):
        raise HostError("INVALID_PARAMS", "properties must be an object")
    try:
        for key, value in properties.items():
            _set_property(modifier, str(key), value)
    except Exception:
        obj.modifiers.remove(modifier)
        raise
    return {"object": object_id(obj), "modifier": {"name": modifier.name, "type": modifier.type}}


def set_properties(params, _runtime):
    obj = resolve_object(params.get("object"))
    modifier = _modifier(obj, params.get("modifier"))
    properties = params.get("properties")
    if not isinstance(properties, dict) or not properties:
        raise HostError("INVALID_PARAMS", "properties must be a non-empty object")
    for key, value in properties.items():
        _set_property(modifier, str(key), value)
    return {"object": object_id(obj), "modifier": modifier.name, "updated": sorted(properties)}


def apply(params, _runtime):
    obj = resolve_object(params.get("object"))
    modifier = _modifier(obj, params.get("modifier"))
    if obj.library is not None:
        raise HostError("UNSUPPORTED", "cannot apply modifiers to a linked-library object")

    # Applying removes the Modifier RNA object from the stack; retain the
    # externally meaningful identity before calling Blender's operator.
    modifier_name = modifier.name
    modifier_type = modifier.type
    with active_object_override(obj):
        obj.modifiers.active = modifier
        if not bpy.ops.object.modifier_apply.poll():
            raise HostError("INVALID_CONTEXT", "modifier_apply is not available for the target object/context")
        result = bpy.ops.object.modifier_apply(modifier=modifier_name, report=False, single_user=bool(params.get("single_user", False)))
        if "FINISHED" not in result:
            raise HostError("HOST_EXCEPTION", "Blender did not apply the modifier")

    payload = {
        "object": object_id(obj),
        "applied": modifier_name,
        "applied_type": modifier_type,
    }
    if obj.type == "MESH":
        payload["mesh_revision"] = bump_mesh_revision(obj)
    return payload


def register(registry) -> None:
    registry.add("modifier.list", list_modifiers, stability="beta")
    registry.add("modifier.add", add, mutating=True, stability="alpha")
    registry.add("modifier.set", set_properties, mutating=True, stability="alpha")
    registry.add("modifier.apply", apply, mutating=True, stability="alpha")
