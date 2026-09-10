"""What the geometry actually measures, in units something else can trust.

Separate from geometry truth because it fails for different reasons. A mesh can
be perfectly manifold, consistently wound and watertight, and still be a chair
eleven metres tall with its pivot floating a metre behind it — and neither of
those is visible in a topology check.

Three claims, deliberately no more:

- **the frame it was measured in.** Reported, never assumed. A dimension without
  a coordinate contract is a number with no currency, and the whole reason the
  contract is pinnable is that a human can change what a unit means between an
  agent's observation and its correction.
- **size and placement.** World-space dimensions and bounds — what a brief means
  by "too tall" — beside the local-space ones a modeller edits, because the two
  differ by exactly the transform an agent is most likely to forget.
- **the transform and the pivot.** A pivot outside its own bounds is the classic
  silently-wrong asset: it imports, renders and animates around nothing.

Explicitly later, and explicitly not fudged here: the Blender→Unity conversion.
The two hosts publish different frames on purpose, and connecting them is a
cross-editor certificate with a proof of its own rather than a multiply buried in
a measurement.
"""
from __future__ import annotations

from typing import Any

from mathutils import Vector

from ..identity import mesh_revision, object_id
from ..recipe import coordinate_contract, units
from .certificate import LOWER_BETTER, NEUTRAL, Measurement


def _bounds(points: list[Vector]) -> dict[str, Any]:
    minimum = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    maximum = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    size = maximum - minimum
    return {
        "min": [round(v, 9) for v in minimum],
        "max": [round(v, 9) for v in maximum],
        "size": [round(v, 9) for v in size],
        "center": [round(v, 9) for v in (minimum + maximum) / 2.0],
    }


def _corners(obj) -> list[Vector]:
    """The object's own bounding box, in world space, transform included."""
    return [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]


def _pivot_offset(obj, world: dict[str, Any]) -> tuple[float, bool]:
    """How far the origin sits from what it is supposed to pivot, and whether it is outside.

    Reported as a distance rather than a verdict. Plenty of assets want an origin
    at the base or at a hinge, so "the pivot is not at the centre" is not an
    error — but "the pivot is not anywhere near the geometry" almost always is,
    and that is the one expressed as an invariant.
    """
    origin = obj.matrix_world.translation
    minimum = Vector(world["min"])
    maximum = Vector(world["max"])
    center = Vector(world["center"])
    inside = all(minimum[axis] - 1e-9 <= origin[axis] <= maximum[axis] + 1e-9 for axis in range(3))
    return round((origin - center).length, 9), inside


def measure(objects: list, *, max_dimension: float | None, runtime) -> Measurement:
    """Size, placement and frame for every subject, plus the combined extent."""
    from .certificate import pins_for

    measurement = Measurement("spatial", {})
    all_corners: list[Vector] = []
    pivots_outside = 0
    non_uniform = 0
    negative_scale = 0
    largest = 0.0

    for obj in objects:
        corners = _corners(obj)
        all_corners.extend(corners)
        world = _bounds(corners)
        local = _bounds([Vector(corner) for corner in obj.bound_box])
        offset, inside = _pivot_offset(obj, world)
        scale = obj.matrix_world.to_scale()
        # Non-uniform and mirrored scale are recorded rather than refused: both
        # are legitimate authoring choices and both change what a downstream
        # importer does with the normals, so an agent should be able to see them.
        uniform = max(abs(scale[a] - scale[b]) for a in range(3) for b in range(3)) < 1e-6
        mirrored = obj.matrix_world.determinant() < 0.0

        measurement.subjects.append({
            "object": object_id(obj),
            "name": obj.name,
            "mesh_revision": mesh_revision(obj),
            "world_bounds": world,
            "local_bounds": local,
            "transform": {
                "location": [round(v, 9) for v in obj.matrix_world.translation],
                "rotation_euler_xyz": [round(v, 9) for v in obj.matrix_world.to_euler()],
                "scale": [round(v, 9) for v in scale],
                "uniform_scale": uniform,
                "mirrored": mirrored,
            },
            "pivot": {
                "world": [round(v, 9) for v in obj.matrix_world.translation],
                "offset_from_bounds_center": offset,
                "inside_bounds": inside,
            },
        })

        pivots_outside += 0 if inside else 1
        non_uniform += 0 if uniform else 1
        negative_scale += 1 if mirrored else 0
        largest = max(largest, max(world["size"]))

    combined = _bounds(all_corners) if all_corners else {
        "min": [0.0] * 3, "max": [0.0] * 3, "size": [0.0] * 3, "center": [0.0] * 3}

    for axis, name in enumerate(("x", "y", "z")):
        measurement.metric(f"spatial.dimension_{name}", combined["size"][axis],
                           direction=NEUTRAL, unit="metre")
    measurement.metric("spatial.largest_dimension", round(largest, 9),
                       direction=NEUTRAL, unit="metre")
    measurement.metric("spatial.pivots_outside_bounds", pivots_outside, direction=LOWER_BETTER)
    measurement.metric("spatial.non_uniform_scales", non_uniform, direction=NEUTRAL)
    measurement.metric("spatial.mirrored_transforms", negative_scale, direction=NEUTRAL)

    measurement.invariant("spatial.pivots_within_bounds", pivots_outside == 0,
                          pivots_outside=pivots_outside)
    measurement.invariant("spatial.non_degenerate_extent",
                          all(size > 0.0 for size in combined["size"]),
                          size=combined["size"])
    if max_dimension is None:
        # A size limit is a brief, not a property of geometry. Without one there
        # is nothing to check, and saying so beats inventing a default nobody
        # asked for and then gating a correction on it.
        measurement.unmeasured("spatial.within_declared_size", "no max_dimension declared")
    else:
        measurement.invariant("spatial.within_declared_size", largest <= max_dimension,
                              largest_dimension=round(largest, 9), max_dimension=max_dimension)

    measurement.subjects.append({
        "object": "__combined__",
        "name": "combined extent",
        "mesh_revision": None,
        "world_bounds": combined,
    })
    measurement.notes.append(
        "dimensions are in this world's coordinate contract; converting them to "
        "another editor's frame is a separate cross-editor certificate, not an "
        "assumption this measurement makes")

    measurement.pins = pins_for(runtime, measurement.subjects)
    measurement.pins["frame"] = {"coordinate_contract": coordinate_contract(), **units()}
    return measurement
