from __future__ import annotations

from collections import deque
from math import inf
from typing import Any

import bmesh
from mathutils import Vector

from ..identity import mesh_revision, object_id
from ..registry import HostError
from .mesh import _mesh_object


def _vec3(value: Any, name: str) -> Vector:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise HostError("INVALID_PARAMS", f"{name} must be a three-number array")
    try:
        return Vector(tuple(float(component) for component in value))
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", f"{name} must contain numbers") from exc


def _range(value: Any, name: str) -> tuple[float, float]:
    if value is None:
        return -inf, inf
    if not isinstance(value, dict):
        raise HostError("INVALID_PARAMS", f"{name} must be an object with optional min/max")
    try:
        lower = float(value.get("min", -inf))
        upper = float(value.get("max", inf))
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", f"{name}.min/max must be numbers") from exc
    if lower > upper:
        raise HostError("INVALID_PARAMS", f"{name}.min cannot exceed max")
    return lower, upper


def _space(params: dict[str, Any]) -> str:
    value = str(params.get("space", "OBJECT")).upper()
    if value not in {"OBJECT", "WORLD"}:
        raise HostError("INVALID_PARAMS", "space must be OBJECT or WORLD")
    return value


def _point(obj, point: Vector, space: str) -> Vector:
    return obj.matrix_world @ point if space == "WORLD" else point.copy()


def _normal(obj, normal: Vector, space: str) -> Vector:
    if space == "WORLD":
        normal = obj.matrix_world.to_3x3().inverted().transposed() @ normal
    if normal.length_squared:
        normal.normalize()
    return normal


def _bounds_filter(point: Vector, raw: Any) -> bool:
    if raw is None:
        return True
    if not isinstance(raw, dict):
        raise HostError("INVALID_PARAMS", "bounds must be an object with optional min/max")
    minimum = _vec3(raw.get("min", [-inf, -inf, -inf]), "bounds.min")
    maximum = _vec3(raw.get("max", [inf, inf, inf]), "bounds.max")
    return all(minimum[i] <= point[i] <= maximum[i] for i in range(3))


def _normal_filter(normal: Vector, raw: Any) -> tuple[bool, float | None]:
    if raw is None:
        return True, None
    if not isinstance(raw, dict):
        raise HostError("INVALID_PARAMS", "normal must be an object")
    direction = _vec3(raw.get("direction"), "normal.direction")
    if direction.length_squared == 0:
        raise HostError("INVALID_PARAMS", "normal.direction cannot be zero")
    direction.normalize()
    try:
        min_dot = float(raw.get("min_dot", 0.9))
        max_dot = float(raw.get("max_dot", 1.0))
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", "normal min_dot/max_dot must be numbers") from exc
    if not -1.0 <= min_dot <= 1.0 or not -1.0 <= max_dot <= 1.0 or min_dot > max_dot:
        raise HostError("INVALID_PARAMS", "normal dot bounds must satisfy -1 <= min_dot <= max_dot <= 1")
    dot = float(normal.dot(direction))
    return min_dot <= dot <= max_dot, dot


def _sort_key(item: dict[str, Any], sort_by: str):
    key = sort_by.lstrip("-").lower()
    if key == "index":
        return item["index"]
    if key in {"area", "length", "valence", "normal_dot"}:
        value = item.get(key)
        return -inf if value is None else value
    if key in {"x", "y", "z"}:
        return item["point"][{"x": 0, "y": 1, "z": 2}[key]]
    raise HostError("INVALID_PARAMS", "sort_by must be index, area, length, valence, normal_dot, x, y, or z (prefix - for descending)")


def query(params, _runtime):
    obj = _mesh_object(params.get("object"))
    domain = str(params.get("domain", "FACE")).upper()
    if domain not in {"VERTEX", "EDGE", "FACE"}:
        raise HostError("INVALID_PARAMS", "domain must be VERTEX, EDGE, or FACE")
    space = _space(params)
    max_results = max(1, min(20_000, int(params.get("max_results", 2_000))))
    details = bool(params.get("details", True))
    selected_filter = params.get("selected")
    hidden_filter = params.get("hidden")
    if selected_filter is not None and not isinstance(selected_filter, bool):
        raise HostError("INVALID_PARAMS", "selected must be boolean when provided")
    if hidden_filter is not None and not isinstance(hidden_filter, bool):
        raise HostError("INVALID_PARAMS", "hidden must be boolean when provided")

    area_range = _range(params.get("area"), "area")
    length_range = _range(params.get("length"), "length")
    valence_range = _range(params.get("valence"), "valence")
    material_index = params.get("material_index")
    if material_index is not None:
        if not isinstance(material_index, int) or isinstance(material_index, bool) or material_index < 0:
            raise HostError("INVALID_PARAMS", "material_index must be a non-negative integer")

    boundary_filter = params.get("boundary")
    manifold_filter = params.get("manifold")
    wire_filter = params.get("wire")
    for label, value in (("boundary", boundary_filter), ("manifold", manifold_filter), ("wire", wire_filter)):
        if value is not None and not isinstance(value, bool):
            raise HostError("INVALID_PARAMS", f"{label} must be boolean when provided")

    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        source = bm.verts if domain == "VERTEX" else bm.edges if domain == "EDGE" else bm.faces
        matches: list[dict[str, Any]] = []
        for element in source:
            if selected_filter is not None and bool(element.select) != selected_filter:
                continue
            if hidden_filter is not None and bool(element.hide) != hidden_filter:
                continue

            if domain == "VERTEX":
                local_point = element.co
                local_normal = element.normal
                valence = len(element.link_edges)
                area = None
                length = None
            elif domain == "EDGE":
                local_point = (element.verts[0].co + element.verts[1].co) * 0.5
                if element.link_faces:
                    local_normal = sum((face.normal for face in element.link_faces), Vector())
                    if local_normal.length_squared:
                        local_normal.normalize()
                else:
                    local_normal = Vector((0.0, 0.0, 0.0))
                valence = len(element.link_faces)
                area = None
                length = float(element.calc_length())
                if boundary_filter is not None and bool(element.is_boundary) != boundary_filter:
                    continue
                if manifold_filter is not None and bool(element.is_manifold) != manifold_filter:
                    continue
                if wire_filter is not None and bool(element.is_wire) != wire_filter:
                    continue
                if not length_range[0] <= length <= length_range[1]:
                    continue
            else:
                local_point = element.calc_center_median()
                local_normal = element.normal
                valence = len(element.verts)
                area = float(element.calc_area())
                length = None
                if material_index is not None and int(element.material_index) != material_index:
                    continue
                if not area_range[0] <= area <= area_range[1]:
                    continue

            if not valence_range[0] <= valence <= valence_range[1]:
                continue
            point = _point(obj, local_point, space)
            if not _bounds_filter(point, params.get("bounds")):
                continue
            normal = _normal(obj, local_normal, space)
            normal_ok, normal_dot = _normal_filter(normal, params.get("normal"))
            if not normal_ok:
                continue

            item = {
                "index": int(element.index),
                "point": [float(value) for value in point],
                "normal": [float(value) for value in normal],
                "valence": int(valence),
            }
            if area is not None:
                item["area"] = area
                item["material_index"] = int(element.material_index)
            if length is not None:
                item["length"] = length
                item["boundary"] = bool(element.is_boundary)
                item["manifold"] = bool(element.is_manifold)
                item["wire"] = bool(element.is_wire)
            if normal_dot is not None:
                item["normal_dot"] = normal_dot
            matches.append(item)

        sort_by = str(params.get("sort_by", "index"))
        matches.sort(key=lambda item: _sort_key(item, sort_by), reverse=sort_by.startswith("-"))
        total = len(matches)
        clipped = matches[:max_results]
        return {
            "object": object_id(obj),
            "mesh_revision": mesh_revision(obj),
            "domain": domain,
            "space": space,
            "count": total,
            "truncated": total > len(clipped),
            "indices": [item["index"] for item in clipped],
            "items": clipped if details else None,
        }
    finally:
        bm.free()


def components(params, _runtime):
    obj = _mesh_object(params.get("object"))
    space = _space(params)
    include_indices = bool(params.get("include_indices", True))
    max_components = max(1, min(10_000, int(params.get("max_components", 1_000))))

    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        unseen = set(bm.verts)
        output: list[dict[str, Any]] = []
        while unseen and len(output) < max_components:
            seed = min(unseen, key=lambda vert: vert.index)
            queue = deque([seed])
            component_verts = []
            unseen.remove(seed)
            while queue:
                vert = queue.popleft()
                component_verts.append(vert)
                for edge in vert.link_edges:
                    other = edge.other_vert(vert)
                    if other in unseen:
                        unseen.remove(other)
                        queue.append(other)

            vert_set = set(component_verts)
            edge_set = {edge for vert in component_verts for edge in vert.link_edges if edge.verts[0] in vert_set and edge.verts[1] in vert_set}
            face_set = {face for vert in component_verts for face in vert.link_faces if all(face_vert in vert_set for face_vert in face.verts)}
            points = [_point(obj, vert.co, space) for vert in component_verts]
            minimum = Vector((min(point.x for point in points), min(point.y for point in points), min(point.z for point in points)))
            maximum = Vector((max(point.x for point in points), max(point.y for point in points), max(point.z for point in points)))
            centroid = sum(points, Vector()) / len(points)
            record: dict[str, Any] = {
                "component": len(output),
                "counts": {"vertices": len(component_verts), "edges": len(edge_set), "faces": len(face_set)},
                "bounds": {"min": list(minimum), "max": list(maximum)},
                "centroid": list(centroid),
            }
            if include_indices:
                record["vertices"] = sorted(vert.index for vert in component_verts)
                record["edges"] = sorted(edge.index for edge in edge_set)
                record["faces"] = sorted(face.index for face in face_set)
            output.append(record)

        output.sort(key=lambda item: (-item["counts"]["vertices"], item["component"]))
        for index, item in enumerate(output):
            item["component"] = index
        return {
            "object": object_id(obj),
            "mesh_revision": mesh_revision(obj),
            "space": space,
            "component_count": len(output) + (1 if unseen else 0),
            "truncated": bool(unseen),
            "components": output,
        }
    finally:
        bm.free()


def register(registry) -> None:
    registry.add("mesh.query", query, stability="alpha")
    registry.add("mesh.components", components, stability="alpha")
