from __future__ import annotations

import bmesh
from mathutils import Vector

from ..identity import assert_mesh_revision, bump_mesh_revision, mesh_revision, object_id
from ..registry import HostError
from .mesh import _mesh_object


def _indices(items, raw, label: str, *, allow_all: bool = False):
    if raw is None and allow_all:
        return list(items)
    if not isinstance(raw, list) or (not raw and not allow_all):
        raise HostError("INVALID_PARAMS", f"{label} must be a non-empty array")
    try:
        return [items[int(index)] for index in raw]
    except (IndexError, ValueError, TypeError) as exc:
        raise HostError("INVALID_PARAMS", f"{label} contains an invalid index") from exc


def _commit(obj, bm, *, topology: bool = True):
    bm.normal_update()
    bm.to_mesh(obj.data)
    obj.data.update()
    result = {"object": object_id(obj), "mesh_revision": mesh_revision(obj)}
    if topology:
        result["mesh_revision"] = bump_mesh_revision(obj)
    result["counts"] = {
        "vertices": len(obj.data.vertices),
        "edges": len(obj.data.edges),
        "faces": len(obj.data.polygons),
    }
    return result


def subdivide_edges(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    cuts = int(params.get("cuts", 1))
    if cuts < 1 or cuts > 1000:
        raise HostError("INVALID_PARAMS", "cuts must be between 1 and 1000")
    smooth = float(params.get("smooth", 0.0))
    if not -1.0 <= smooth <= 1.0:
        raise HostError("INVALID_PARAMS", "smooth must be between -1 and 1")
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        edges = _indices(bm.edges, params.get("edge_indices"), "edge_indices")
        result = bmesh.ops.subdivide_edges(
            bm,
            edges=edges,
            cuts=cuts,
            smooth=smooth,
            use_grid_fill=bool(params.get("use_grid_fill", False)),
            use_single_edge=bool(params.get("use_single_edge", False)),
            use_only_quads=bool(params.get("use_only_quads", False)),
        )
        payload = _commit(obj, bm)
        payload["new_geometry"] = len(result.get("geom_inner", [])) + len(result.get("geom_split", []))
        return payload
    finally:
        bm.free()


def inset_faces(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    thickness = float(params.get("thickness", 0.0))
    depth = float(params.get("depth", 0.0))
    if thickness < 0:
        raise HostError("INVALID_PARAMS", "thickness must be non-negative")
    mode = str(params.get("mode", "REGION")).upper()
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        faces = _indices(bm.faces, params.get("face_indices"), "face_indices")
        common = {
            "faces": faces,
            "thickness": thickness,
            "depth": depth,
            "use_even_offset": bool(params.get("use_even_offset", True)),
            "use_interpolate": bool(params.get("use_interpolate", True)),
            "use_relative_offset": bool(params.get("use_relative_offset", False)),
        }
        if mode == "REGION":
            result = bmesh.ops.inset_region(
                bm,
                **common,
                use_boundary=bool(params.get("use_boundary", True)),
                use_edge_rail=bool(params.get("use_edge_rail", False)),
                use_outset=bool(params.get("use_outset", False)),
            )
        elif mode == "INDIVIDUAL":
            result = bmesh.ops.inset_individual(bm, **common)
        else:
            raise HostError("INVALID_PARAMS", "mode must be REGION or INDIVIDUAL")
        payload = _commit(obj, bm)
        payload["output_faces"] = len(result.get("faces", []))
        return payload
    finally:
        bm.free()


def bridge_loops(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.edges.ensure_lookup_table()
        edges = _indices(bm.edges, params.get("edge_indices"), "edge_indices")
        result = bmesh.ops.bridge_loops(
            bm,
            edges=edges,
            use_pairs=bool(params.get("use_pairs", False)),
            use_cyclic=bool(params.get("use_cyclic", False)),
            use_merge=bool(params.get("use_merge", False)),
            merge_factor=float(params.get("merge_factor", 0.5)),
            twist_offset=int(params.get("twist_offset", 0)),
        )
        payload = _commit(obj, bm)
        payload["output"] = {"faces": len(result.get("faces", [])), "edges": len(result.get("edges", []))}
        return payload
    finally:
        bm.free()


def merge_by_distance(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    distance = float(params.get("distance", 0.0001))
    if distance < 0:
        raise HostError("INVALID_PARAMS", "distance must be non-negative")
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        verts = _indices(bm.verts, params.get("vertex_indices"), "vertex_indices", allow_all=True)
        before = len(bm.verts)
        bmesh.ops.remove_doubles(
            bm,
            verts=verts,
            dist=distance,
            use_connected=bool(params.get("use_connected", False)),
        )
        payload = _commit(obj, bm)
        payload["removed_vertices"] = before - len(bm.verts)
        return payload
    finally:
        bm.free()


def triangulate(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    quad_method = str(params.get("quad_method", "BEAUTY")).upper()
    ngon_method = str(params.get("ngon_method", "BEAUTY")).upper()
    if quad_method not in {"BEAUTY", "FIXED", "ALTERNATE", "SHORT_EDGE", "LONG_EDGE"}:
        raise HostError("INVALID_PARAMS", "invalid quad_method")
    if ngon_method not in {"BEAUTY", "EAR_CLIP"}:
        raise HostError("INVALID_PARAMS", "invalid ngon_method")
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        faces = _indices(bm.faces, params.get("face_indices"), "face_indices", allow_all=True)
        result = bmesh.ops.triangulate(bm, faces=faces, quad_method=quad_method, ngon_method=ngon_method)
        payload = _commit(obj, bm)
        payload["output_faces"] = len(result.get("faces", []))
        return payload
    finally:
        bm.free()


def recalc_normals(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        faces = _indices(bm.faces, params.get("face_indices"), "face_indices", allow_all=True)
        bmesh.ops.recalc_face_normals(bm, faces=faces)
        payload = _commit(obj, bm, topology=False)
        payload["faces_processed"] = len(faces)
        return payload
    finally:
        bm.free()


def bisect_plane(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    plane_co = params.get("plane_co", [0.0, 0.0, 0.0])
    plane_no = params.get("plane_no")
    if not isinstance(plane_co, list) or len(plane_co) != 3 or not isinstance(plane_no, list) or len(plane_no) != 3:
        raise HostError("INVALID_PARAMS", "plane_co and plane_no must be three-number arrays")
    normal = Vector(tuple(float(value) for value in plane_no))
    if normal.length_squared == 0:
        raise HostError("INVALID_PARAMS", "plane_no cannot be zero")
    normal.normalize()
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
        result = bmesh.ops.bisect_plane(
            bm,
            geom=geom,
            dist=max(0.0, float(params.get("distance", 0.00001))),
            plane_co=tuple(float(value) for value in plane_co),
            plane_no=normal,
            use_snap_center=bool(params.get("use_snap_center", False)),
            clear_outer=bool(params.get("clear_outer", False)),
            clear_inner=bool(params.get("clear_inner", False)),
        )
        payload = _commit(obj, bm)
        payload["cut_geometry"] = len(result.get("geom_cut", []))
        return payload
    finally:
        bm.free()


def solidify(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    thickness = float(params.get("thickness", 0.01))
    if thickness == 0:
        raise HostError("INVALID_PARAMS", "thickness cannot be zero")
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.faces.ensure_lookup_table()
        faces = _indices(bm.faces, params.get("face_indices"), "face_indices", allow_all=True)
        result = bmesh.ops.solidify(bm, geom=faces, thickness=thickness)
        payload = _commit(obj, bm)
        payload["output_geometry"] = len(result.get("geom", []))
        return payload
    finally:
        bm.free()


def symmetrize(params, runtime):
    obj = _mesh_object(params.get("object"), mutating=True, runtime=runtime)
    assert_mesh_revision(obj, params.get("expected_mesh_revision"))
    direction = str(params.get("direction", "-X")).upper()
    if direction not in {"-X", "-Y", "-Z", "X", "Y", "Z"}:
        raise HostError("INVALID_PARAMS", "direction must be one of -X,-Y,-Z,X,Y,Z")
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
        result = bmesh.ops.symmetrize(
            bm,
            input=geom,
            direction=direction,
            dist=max(0.0, float(params.get("distance", 0.0001))),
            use_shapekey=bool(params.get("use_shapekey", False)),
        )
        payload = _commit(obj, bm)
        payload["output_geometry"] = len(result.get("geom", []))
        return payload
    finally:
        bm.free()


def register(registry) -> None:
    registry.add("mesh.subdivide_edges", subdivide_edges, mutating=True, stability="alpha")
    registry.add("mesh.inset_faces", inset_faces, mutating=True, stability="alpha")
    registry.add("mesh.bridge_loops", bridge_loops, mutating=True, stability="alpha")
    registry.add("mesh.merge_by_distance", merge_by_distance, mutating=True, stability="alpha")
    registry.add("mesh.triangulate", triangulate, mutating=True, stability="alpha")
    registry.add("mesh.recalc_normals", recalc_normals, mutating=True, stability="alpha")
    registry.add("mesh.bisect_plane", bisect_plane, mutating=True, stability="alpha")
    registry.add("mesh.solidify", solidify, mutating=True, stability="alpha")
    registry.add("mesh.symmetrize", symmetrize, mutating=True, stability="alpha")
