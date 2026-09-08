from __future__ import annotations

import bmesh
import bpy
from mathutils import Vector

from ..identity import assert_mesh_revision, bump_mesh_revision, mesh_revision, object_id, resolve_object
from ..registry import HostError


def _mesh_object(ref):
    obj = resolve_object(ref)
    if obj.type != "MESH" or obj.data is None:
        raise HostError("INVALID_PARAMS", "object is not a mesh")
    if obj.data.library is not None:
        raise HostError("UNSUPPORTED", "linked-library mesh data is read-only")
    return obj


def inspect(params, _runtime):
    obj = _mesh_object(params.get("object"))
    mesh = obj.data
    result = {
        "object": object_id(obj),
        "mesh": mesh.name_full,
        "mesh_revision": mesh_revision(obj),
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "materials": [material.name_full if material else None for material in mesh.materials],
    }
    if bool(params.get("evaluated", False)):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = obj.evaluated_get(depsgraph)
        evaluated_mesh = evaluated.to_mesh()
        try:
            result["evaluated"] = {
                "vertices": len(evaluated_mesh.vertices),
                "edges": len(evaluated_mesh.edges),
                "faces": len(evaluated_mesh.polygons),
            }
        finally:
            evaluated.to_mesh_clear()
    return result


def elements(params, _runtime):
    obj = _mesh_object(params.get("object"))
    kind = str(params.get("kind", "vertices")).lower()
    offset = max(0, int(params.get("offset", 0)))
    limit = max(1, min(2000, int(params.get("limit", 500))))
    mesh = obj.data

    if kind == "vertices":
        source = mesh.vertices
        stop = min(len(source), offset + limit)
        items = [
            {"index": source[index].index, "co": list(source[index].co), "normal": list(source[index].normal)}
            for index in range(min(offset, len(source)), stop)
        ]
    elif kind == "edges":
        source = mesh.edges
        stop = min(len(source), offset + limit)
        items = [
            {
                "index": source[index].index,
                "vertices": list(source[index].vertices),
                "crease": float(getattr(source[index], "crease", 0.0)),
            }
            for index in range(min(offset, len(source)), stop)
        ]
    elif kind == "faces":
        source = mesh.polygons
        stop = min(len(source), offset + limit)
        items = [
            {
                "index": source[index].index,
                "vertices": list(source[index].vertices),
                "normal": list(source[index].normal),
                "material_index": source[index].material_index,
                "area": float(source[index].area),
            }
            for index in range(min(offset, len(source)), stop)
        ]
    else:
        raise HostError("INVALID_PARAMS", "kind must be vertices, edges, or faces")

    return {
        "object": object_id(obj),
        "mesh_revision": mesh_revision(obj),
        "kind": kind,
        "offset": offset,
        "limit": limit,
        "total": len(source),
        "items": items,
        "has_more": offset + len(items) < len(source),
    }


def validate(params, _runtime):
    obj = _mesh_object(params.get("object"))
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        epsilon = float(params.get("epsilon", 1e-10))
        non_manifold = [edge.index for edge in bm.edges if not edge.is_manifold]
        boundary = [edge.index for edge in bm.edges if edge.is_boundary]
        wire = [edge.index for edge in bm.edges if edge.is_wire]
        loose_vertices = [vert.index for vert in bm.verts if not vert.link_edges]
        zero_edges = [edge.index for edge in bm.edges if edge.calc_length() <= epsilon]
        degenerate_faces = [face.index for face in bm.faces if face.calc_area() <= epsilon]
        ngons = [face.index for face in bm.faces if len(face.verts) > 4]
        triangles = sum(1 for face in bm.faces if len(face.verts) == 3)
        quads = sum(1 for face in bm.faces if len(face.verts) == 4)
        return {
            "object": object_id(obj),
            "mesh_revision": mesh_revision(obj),
            "valid": not zero_edges and not degenerate_faces,
            "manifold": not non_manifold,
            "counts": {
                "vertices": len(bm.verts),
                "edges": len(bm.edges),
                "faces": len(bm.faces),
                "triangles": triangles,
                "quads": quads,
                "ngons": len(ngons),
            },
            "issues": {
                "non_manifold_edges": non_manifold,
                "boundary_edges": boundary,
                "wire_edges": wire,
                "loose_vertices": loose_vertices,
                "zero_length_edges": zero_edges,
                "degenerate_faces": degenerate_faces,
                "ngons": ngons,
            },
        }
    finally:
        bm.free()


def bevel(params, _runtime):
    obj = _mesh_object(params.get("object"))
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    indices = params.get("edge_indices")
    if not isinstance(indices, list) or not indices:
        raise HostError("INVALID_PARAMS", "edge_indices must be a non-empty array")
    width = float(params.get("width", 0.01))
    segments = int(params.get("segments", 1))
    if width <= 0 or segments < 1:
        raise HostError("INVALID_PARAMS", "width must be positive and segments >= 1")

    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        try:
            selected = [bm.edges[int(index)] for index in indices]
        except (IndexError, ValueError, TypeError) as exc:
            raise HostError("INVALID_PARAMS", "edge_indices contains an invalid edge index") from exc
        bmesh.ops.bevel(
            bm,
            geom=selected,
            offset=width,
            offset_type="OFFSET",
            segments=segments,
            affect="EDGES",
            clamp_overlap=bool(params.get("clamp_overlap", True)),
        )
        bm.normal_update()
        bm.to_mesh(obj.data)
        obj.data.update()
    finally:
        bm.free()
    return {"object": object_id(obj), "mesh_revision": bump_mesh_revision(obj)}


def extrude_faces(params, _runtime):
    obj = _mesh_object(params.get("object"))
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    indices = params.get("face_indices")
    translation = params.get("translation", [0.0, 0.0, 0.0])
    if not isinstance(indices, list) or not indices:
        raise HostError("INVALID_PARAMS", "face_indices must be a non-empty array")
    if not isinstance(translation, list) or len(translation) != 3:
        raise HostError("INVALID_PARAMS", "translation must be a three-number array")
    vector = Vector(tuple(float(v) for v in translation))

    bm = bmesh.new()
    created_vertices: list[int] = []
    created_edges: list[int] = []
    created_faces: list[int] = []
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        try:
            # Deduplicate while retaining the caller's semantic selection. Passing
            # the source faces alone follows Blender's face-region extrusion
            # operator; the original source faces are removed after translation
            # so a closed manifold does not retain hidden internal faces.
            selected = list(dict.fromkeys(bm.faces[int(index)] for index in indices))
        except (IndexError, ValueError, TypeError) as exc:
            raise HostError("INVALID_PARAMS", "face_indices contains an invalid face index") from exc

        result = bmesh.ops.extrude_face_region(bm, geom=selected, use_keep_orig=False)
        new_vertices = [element for element in result["geom"] if isinstance(element, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, verts=new_vertices, vec=vector)
        bmesh.ops.delete(bm, geom=selected, context="FACES")
        bm.normal_update()

        # Return revision-scoped handles to surviving geometry from the newly
        # extruded region. Any source elements deleted above are filtered out.
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        created_vertices = sorted(
            element.index for element in result["geom"]
            if isinstance(element, bmesh.types.BMVert) and element.is_valid
        )
        created_edges = sorted(
            element.index for element in result["geom"]
            if isinstance(element, bmesh.types.BMEdge) and element.is_valid
        )
        created_faces = sorted(
            element.index for element in result["geom"]
            if isinstance(element, bmesh.types.BMFace) and element.is_valid
        )

        bm.to_mesh(obj.data)
        obj.data.update()
    finally:
        bm.free()

    revision = bump_mesh_revision(obj)
    return {
        "object": object_id(obj),
        "mesh_revision": revision,
        "translation": list(vector),
        "created": {
            "vertices": created_vertices,
            "edges": created_edges,
            "faces": created_faces,
        },
    }


def register(registry) -> None:
    registry.add("mesh.inspect", inspect, stability="beta")
    registry.add("mesh.elements", elements, stability="alpha")
    registry.add("mesh.validate", validate, stability="beta")
    registry.add("mesh.bevel", bevel, mutating=True, stability="alpha")
    registry.add("mesh.extrude_faces", extrude_faces, mutating=True, stability="alpha")
