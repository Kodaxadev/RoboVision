"""Whether a repeated structure is actually repeated, and actually organised.

A model can decide aesthetically that a radiator needs eight fins. Deciding it is
the easy half. Generative and hand-built geometry both drift here in ways that
look fine in a thumbnail and fall apart under inspection: seven fins instead of
eight, one of them a millimetre out of line, one rotated a degree, one slightly
larger than its neighbours. This measures the declared intent against what is
actually there.

Declared, not discovered. Unsupervised detection of every repeated structure in
arbitrary geometry is a much larger problem, and getting it wrong would produce
confident nonsense about geometry nobody claimed was patterned. So the caller
says what it expects — a count, an axis, a spacing, tolerances — and this reports
whether the geometry satisfies it. Inferring the declaration is later work.

Members come either from separate objects or from the connected islands of one
mesh, because arrays are built both ways and a measurement that understood only
one of them would be useless on half of real assets.
"""
from __future__ import annotations

from collections import deque
from math import atan2, degrees
from typing import Any

import bmesh
from mathutils import Vector

from ..identity import mesh_revision, object_id
from ..registry import HostError
from .certificate import NEUTRAL, Measurement
from .pattern_metrics import record_dimensions, record_orientation, record_spacing

PATTERN_SCHEMA = 1

LINEAR = "linear"
RADIAL = "radial"
MIRROR = "mirror"
KINDS = (LINEAR, RADIAL, MIRROR)


def _members_from_components(obj) -> list[dict[str, Any]]:
    """Each connected island of one mesh, which is how an array is usually built."""
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        matrix = obj.matrix_world
        unseen = set(bm.verts)
        members: list[dict[str, Any]] = []
        while unseen:
            seed = min(unseen, key=lambda vert: vert.index)
            unseen.remove(seed)
            queue = deque([seed])
            island = []
            while queue:
                vert = queue.popleft()
                island.append(vert)
                for edge in vert.link_edges:
                    other = edge.other_vert(vert)
                    if other in unseen:
                        unseen.remove(other)
                        queue.append(other)
            points = [matrix @ vert.co for vert in island]
            members.append(_describe(points, f"component:{len(members)}", object_id(obj)))
        return members
    finally:
        bm.free()


def _members_from_objects(objects: list) -> list[dict[str, Any]]:
    members = []
    for obj in objects:
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            points = [obj.matrix_world @ vert.co for vert in bm.verts]
        finally:
            bm.free()
        if not points:
            raise HostError("INVALID_PARAMS", f"{obj.name} has no geometry to pattern")
        members.append(_describe(points, obj.name, object_id(obj)))
    return members


def _describe(points: list[Vector], label: str, owner: str) -> dict[str, Any]:
    """A member reduced to what a pattern is about: where, how big, which way.

    The orientation is the principal axis of the member's own point cloud rather
    than a transform, because an array baked into one mesh has no per-member
    transform to read — and a fin rotated after the array was built would be
    invisible to a check that read transforms.
    """
    centroid = sum(points, Vector()) / len(points)
    minimum = Vector((min(p.x for p in points), min(p.y for p in points),
                      min(p.z for p in points)))
    maximum = Vector((max(p.x for p in points), max(p.y for p in points),
                      max(p.z for p in points)))
    frame = _principal_frame(points, centroid)
    oriented = _oriented_size(points, centroid, frame)
    return {
        "member": label,
        "object": owner,
        "centroid": centroid,
        # World-aligned, for a caller that wants to know where the member sits.
        "size": maximum - minimum,
        "oriented_size": oriented,
        # The number dimensional consistency is judged on. Measured in the
        # member's own principal frame rather than in world axes, because a
        # world-aligned box grows when a member is merely rotated: a fin turned
        # six degrees reported an 8% dimensional inconsistency while being
        # exactly the same size as its siblings, which made rotation and
        # resizing — two different corrections — one number.
        "extent": oriented.length,
        "axis": frame[0],
        "points": len(points),
    }


def _covariance(points: list[Vector], centroid: Vector) -> list[list[float]]:
    matrix = [[0.0] * 3 for _ in range(3)]
    for point in points:
        delta = point - centroid
        for i in range(3):
            for j in range(3):
                matrix[i][j] += delta[i] * delta[j]
    return matrix


def _dominant(matrix: list[list[float]], seed: Vector) -> Vector:
    vector = seed.normalized()
    for _ in range(32):
        nxt = Vector((
            sum(matrix[0][j] * vector[j] for j in range(3)),
            sum(matrix[1][j] * vector[j] for j in range(3)),
            sum(matrix[2][j] * vector[j] for j in range(3)),
        ))
        if nxt.length < 1e-12:
            return Vector((1.0, 0.0, 0.0))
        vector = nxt.normalized()
    # An axis has no sign; canonicalising it stops two identical members
    # reporting a 180 degree disagreement.
    if (vector.x, vector.y, vector.z) < (0.0, 0.0, 0.0):
        vector = -vector
    return vector


def _principal_frame(points: list[Vector], centroid: Vector):
    """The member's own axes, longest first, by deflated power iteration.

    A full frame rather than a single axis, because dimensional consistency has
    to be measured in a space that turns with the member. It still does not
    resolve a rotation about the long axis of a rotationally symmetric member,
    and that stays declared as a limit.
    """
    matrix = _covariance(points, centroid)
    first = _dominant(matrix, Vector((1.0, 0.7, 0.3)))
    dominant_value = _eigenvalue(matrix, first)
    for i in range(3):
        for j in range(3):
            matrix[i][j] -= dominant_value * first[i] * first[j]
    second = _dominant(matrix, Vector((0.3, 1.0, 0.7)))
    second = second - first * second.dot(first)
    second = second.normalized() if second.length > 1e-9 else _any_perpendicular(first)
    return first, second, first.cross(second).normalized()


def _eigenvalue(matrix: list[list[float]], vector: Vector) -> float:
    product = Vector((
        sum(matrix[0][j] * vector[j] for j in range(3)),
        sum(matrix[1][j] * vector[j] for j in range(3)),
        sum(matrix[2][j] * vector[j] for j in range(3)),
    ))
    return product.dot(vector)


def _any_perpendicular(axis: Vector) -> Vector:
    reference = Vector((0.0, 0.0, 1.0))
    if abs(axis.dot(reference)) > 0.9:
        reference = Vector((0.0, 1.0, 0.0))
    return (reference - axis * reference.dot(axis)).normalized()


def _oriented_size(points: list[Vector], centroid: Vector, frame) -> Vector:
    """Extent along the member's own axes, which a rotated copy shares exactly."""
    spans = []
    for axis in frame:
        projected = [(point - centroid).dot(axis) for point in points]
        spans.append(max(projected) - min(projected))
    return Vector(spans)


def _axis_vector(raw: Any) -> Vector:
    if isinstance(raw, str):
        named = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
        key = raw.lower().replace("world_", "").replace("local_", "")
        if key not in named:
            raise HostError("INVALID_PARAMS", f"unknown axis: {raw}")
        return Vector(named[key])
    if isinstance(raw, list) and len(raw) == 3:
        vector = Vector([float(v) for v in raw])
        if vector.length < 1e-9:
            raise HostError("INVALID_PARAMS", "axis must be a non-zero vector")
        return vector.normalized()
    raise HostError("INVALID_PARAMS", "axis must be x/y/z or a 3-vector")


def _linear_gaps(members: list[dict[str, Any]], axis: Vector):
    """Consecutive gaps along the declared axis, in the order they occur on it."""
    ordered = sorted(members, key=lambda member: member["centroid"].dot(axis))
    positions = [member["centroid"].dot(axis) for member in ordered]
    return [positions[i + 1] - positions[i] for i in range(len(positions) - 1)], ordered


def _radial_gaps(members: list[dict[str, Any]], center: Vector, axis: Vector):
    """Angular gaps around the declared axis, in degrees, closing the circle."""
    reference = Vector((1.0, 0.0, 0.0))
    if abs(axis.dot(reference)) > 0.9:
        reference = Vector((0.0, 1.0, 0.0))
    basis_x = (reference - axis * reference.dot(axis)).normalized()
    basis_y = axis.cross(basis_x)
    angles = []
    for member in members:
        delta = member["centroid"] - center
        planar = delta - axis * delta.dot(axis)
        angles.append((degrees(atan2(planar.dot(basis_y), planar.dot(basis_x))) % 360.0,
                       member))
    angles.sort(key=lambda entry: entry[0])
    values = [angle for angle, _ in angles]
    gaps = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    # The wrap-around gap is a real gap. Without it a radial array missing one
    # member looks evenly spaced with one fewer interval.
    gaps.append(360.0 - values[-1] + values[0])
    return gaps, [member for _, member in angles]


def _mirror_residuals(members: list[dict[str, Any]], declaration: dict[str, Any]):
    """How far each member is from where its mirrored partner should be.

    A pair that is nearly but not exactly symmetric is the interesting case: it
    reads as deliberate at a glance and is measurably wrong.
    """
    point = declaration["center"]
    normal = declaration["axis"]
    residuals = []
    for member in members:
        delta = member["centroid"] - point
        mirrored = member["centroid"] - normal * (2.0 * delta.dot(normal))
        others = [other for other in members if other is not member]
        if not others:
            residuals.append(float("inf"))
            continue
        nearest = min(others, key=lambda other: (other["centroid"] - mirrored).length)
        residuals.append((nearest["centroid"] - mirrored).length)
    return residuals, list(members)


def measure(members: list[dict[str, Any]], declaration: dict[str, Any],
            runtime, subjects: list[dict[str, Any]]) -> Measurement:
    """Compare a declared pattern against the geometry meant to satisfy it."""
    from .certificate import pins_for

    measurement = Measurement("pattern", {})
    kind = declaration["kind"]
    expected = declaration["count"]
    found = len(members)

    measurement.metric("pattern.member_count", found, direction=NEUTRAL)
    # Binary because it is: seven fins where eight were required is a missing
    # fin, not a fin that needs nudging.
    measurement.invariant("pattern.count_matches", found == expected,
                          expected=expected, found=found)

    if found < 2:
        for name in ("pattern.spacing_within_tolerance",
                     "pattern.orientation_within_tolerance",
                     "pattern.dimensions_consistent"):
            measurement.unmeasured(name, "at least two members are needed")
        measurement.pins = pins_for(runtime, subjects)
        measurement.pins["pattern"] = {"schema": PATTERN_SCHEMA,
                                       **_declaration_record(declaration)}
        return measurement

    if kind == RADIAL:
        gaps, ordered = _radial_gaps(members, declaration["center"], declaration["axis"])
        unit, target = "degree", declaration.get("spacing")
    elif kind == LINEAR:
        gaps, ordered = _linear_gaps(members, declaration["axis"])
        unit, target = "metre", declaration.get("spacing")
    else:
        gaps, ordered = _mirror_residuals(members, declaration)
        unit, target = "metre", 0.0

    record_spacing(measurement, gaps, target, unit, declaration)
    record_orientation(measurement, ordered, declaration)
    record_dimensions(measurement, ordered, declaration)

    measurement.subjects = subjects + [{
        "object": "__members__",
        "mesh_revision": None,
        "members": [{
            "member": member["member"],
            "object": member["object"],
            "centroid": [round(v, 9) for v in member["centroid"]],
            "size": [round(v, 9) for v in member["size"]],
            "oriented_size": [round(v, 9) for v in member["oriented_size"]],
            "axis": [round(v, 9) for v in member["axis"]],
        } for member in ordered],
        "gaps": [round(gap, 9) for gap in gaps],
    }]
    measurement.unmeasured(
        "pattern.member_roll",
        "member orientation is compared by principal axis, so a rotation about a "
        "member's own long axis is not detected")
    measurement.pins = pins_for(runtime, subjects)
    measurement.pins["pattern"] = {"schema": PATTERN_SCHEMA,
                                   **_declaration_record(declaration)}
    return measurement


def _declaration_record(declaration: dict[str, Any]) -> dict[str, Any]:
    record = dict(declaration)
    for key in ("axis", "center"):
        if isinstance(record.get(key), Vector):
            record[key] = [round(v, 9) for v in record[key]]
    return record


def resolve(params: dict[str, Any]):
    """Read the declaration and gather the members it is about."""
    from ..identity import resolve_object

    kind = str(params.get("kind", LINEAR))
    if kind not in KINDS:
        raise HostError("INVALID_PARAMS", f"kind must be one of {KINDS}")
    count = params.get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise HostError("INVALID_PARAMS", "count is required and must be a positive integer")

    subjects: list[dict[str, Any]] = []
    if params.get("components_of"):
        obj = resolve_object(params["components_of"])
        if obj.type != "MESH":
            raise HostError("INVALID_PARAMS", "components_of must name a mesh")
        members = _members_from_components(obj)
        subjects.append({"object": object_id(obj), "name": obj.name,
                         "mesh_revision": mesh_revision(obj)})
    else:
        refs = params.get("members")
        if not isinstance(refs, list) or not refs:
            raise HostError("INVALID_PARAMS", "members or components_of is required")
        objects = [resolve_object(ref) for ref in refs]
        for obj in objects:
            if obj.type != "MESH":
                raise HostError("INVALID_PARAMS", f"{obj.name} is not a mesh")
            subjects.append({"object": object_id(obj), "name": obj.name,
                             "mesh_revision": mesh_revision(obj)})
        members = _members_from_objects(objects)

    declaration: dict[str, Any] = {"kind": kind, "count": count,
                                   "axis": _axis_vector(params.get("axis", "x"))}
    center = params.get("center")
    if center is not None:
        declaration["center"] = Vector([float(v) for v in center])
    elif kind in (RADIAL, MIRROR):
        declaration["center"] = (sum((member["centroid"] for member in members), Vector())
                                 / max(1, len(members)))
    for key in ("spacing", "spacing_tolerance", "orientation_tolerance",
                "dimension_variance"):
        if params.get(key) is not None:
            declaration[key] = float(params[key])
    if kind == RADIAL and declaration.get("spacing") is None:
        # The pitch a radial array of this count implies, so a caller need not
        # restate arithmetic the declaration already determines.
        declaration["spacing"] = 360.0 / count
    return members, declaration, subjects
