from __future__ import annotations

import bmesh
import bpy
from mathutils import Matrix

from ..identity import OBJECT_ID_KEY, object_id, resolve_object
from ..registry import HostError
from ..snapshots import object_snapshot


def _vec3(value, name: str):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise HostError("INVALID_PARAMS", f"{name} must be a three-number array")
    try:
        return tuple(float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", f"{name} must contain numbers") from exc


def _sync_transform_state() -> None:
    """Force Blender to publish derived transform state before inspection.

    Assigning location/rotation/scale dirties the dependency graph, while
    Object.matrix_world can still reflect the previous evaluated transform until
    the view layer is updated. RoboVision responses are contracts, so local and
    world transform fields must describe the same editor state.
    """
    view_layer = getattr(bpy.context, "view_layer", None)
    if view_layer is not None:
        view_layer.update()


def inspect(params, _runtime):
    obj = resolve_object(params.get("object"))
    _sync_transform_state()
    return object_snapshot(obj, deep=bool(params.get("deep", True)))


def create(params, _runtime):
    kind = str(params.get("kind", "cube")).lower()
    name = str(params.get("name") or "RoboVisionObject")

    if kind == "empty":
        obj = bpy.data.objects.new(name, None)
    elif kind == "cube":
        size = float(params.get("size", 1.0))
        if size <= 0:
            raise HostError("INVALID_PARAMS", "size must be positive")
        mesh = bpy.data.meshes.new(name + "Mesh")
        bm = bmesh.new()
        try:
            bmesh.ops.create_cube(bm, size=size, matrix=Matrix.Identity(4), calc_uvs=True)
            bm.to_mesh(mesh)
        finally:
            bm.free()
        obj = bpy.data.objects.new(name, mesh)
    elif kind == "mesh":
        vertices = params.get("vertices", [])
        edges = params.get("edges", [])
        faces = params.get("faces", [])
        if not isinstance(vertices, list) or not isinstance(edges, list) or not isinstance(faces, list):
            raise HostError("INVALID_PARAMS", "vertices, edges and faces must be arrays")
        mesh = bpy.data.meshes.new(name + "Mesh")
        try:
            mesh.from_pydata(vertices, edges, faces)
            mesh.update(calc_edges=True)
        except Exception:
            bpy.data.meshes.remove(mesh)
            raise
        obj = bpy.data.objects.new(name, mesh)
    else:
        raise HostError("INVALID_PARAMS", f"unsupported object kind: {kind}")

    collection = bpy.context.collection or bpy.context.scene.collection
    collection.objects.link(obj)
    if "location" in params:
        obj.location = _vec3(params["location"], "location")
    if "rotation" in params:
        obj.rotation_euler = _vec3(params["rotation"], "rotation")
    if "scale" in params:
        obj.scale = _vec3(params["scale"], "scale")
    _sync_transform_state()
    return object_snapshot(obj, deep=True)


def delete(params, _runtime):
    obj = resolve_object(params.get("object"))
    if obj.library is not None:
        raise HostError("UNSUPPORTED", "cannot delete a linked-library object through this operation")
    result = {"id": object_id(obj), "name": obj.name_full}
    bpy.data.objects.remove(obj, do_unlink=True)
    return {"deleted": result}


def duplicate(params, _runtime):
    source = resolve_object(params.get("object"))
    clone = source.copy()
    if source.data is not None and bool(params.get("copy_data", True)):
        clone.data = source.data.copy()
    if OBJECT_ID_KEY in clone:
        del clone[OBJECT_ID_KEY]
    clone.name = str(params.get("name") or (source.name + "_copy"))
    target_collection = source.users_collection[0] if source.users_collection else (bpy.context.collection or bpy.context.scene.collection)
    target_collection.objects.link(clone)
    object_id(clone)
    _sync_transform_state()
    return object_snapshot(clone, deep=True)


def transform(params, _runtime):
    obj = resolve_object(params.get("object"))
    if "matrix_world" in params:
        matrix = params["matrix_world"]
        if not isinstance(matrix, list) or len(matrix) != 4 or any(not isinstance(row, list) or len(row) != 4 for row in matrix):
            raise HostError("INVALID_PARAMS", "matrix_world must be a 4x4 number array")
        obj.matrix_world = Matrix([[float(v) for v in row] for row in matrix])
    else:
        if "location" in params:
            obj.location = _vec3(params["location"], "location")
        if "rotation" in params:
            obj.rotation_euler = _vec3(params["rotation"], "rotation")
        if "scale" in params:
            obj.scale = _vec3(params["scale"], "scale")
    _sync_transform_state()
    return object_snapshot(obj, deep=False)


def parent(params, _runtime):
    child = resolve_object(params.get("object"))
    parent_ref = params.get("parent")
    new_parent = resolve_object(parent_ref) if parent_ref is not None else None
    if new_parent is child:
        raise HostError("INVALID_PARAMS", "object cannot be parented to itself")
    preserve_world = bool(params.get("preserve_world", True))
    world = child.matrix_world.copy()
    child.parent = new_parent
    if preserve_world:
        child.matrix_world = world
    _sync_transform_state()
    return object_snapshot(child, deep=False)


def register(registry) -> None:
    registry.add("object.inspect", inspect, stability="beta")
    registry.add("object.create", create, mutating=True, stability="alpha")
    registry.add("object.delete", delete, mutating=True, stability="alpha")
    registry.add("object.duplicate", duplicate, mutating=True, stability="alpha")
    registry.add("object.transform", transform, mutating=True, stability="alpha")
    registry.add("object.parent", parent, mutating=True, stability="alpha")
