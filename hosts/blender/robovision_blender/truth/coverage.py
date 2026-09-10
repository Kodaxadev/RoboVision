"""Which parts of the asset have actually been looked at, and which have not.

Coverage means surface evidence. "We rendered forty-two screenshots" is a
statement about effort, not about the asset — an underside can survive any number
of renders that all happen to look down. So each measurement samples the actual
surface, area-weighted, and asks of every sample whether some canonical camera
could really see it: front-facing *and* unoccluded, tested by casting a ray back
toward the camera through the whole subject set so one part shadowing another
counts as the occlusion it is.

Deliberately geometric rather than rendered. The visibility test needs no
graphics context at all, which is what keeps the definition of the observation
independent of whether a display happened to be available — and it means coverage
is reproducible in CI, in a background editor, and on a machine with no GPU. The
cameras are the same ones a silhouette render will use, so the two kinds of
evidence share one provenance record.

It is a sample, and it says so. `limits` carries the approximation rather than
letting a fraction computed from four thousand points read like a continuous
proof.
"""
from __future__ import annotations

from typing import Any

import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ..identity import mesh_revision, object_id
from .certificate import HIGHER_BETTER, Measurement
from .coverage_regions import next_view, record as _record, regions as _regions
from . import views as canonical_views

COVERAGE_SCHEMA = 1

# How far off the surface a visibility ray starts. Large enough that a ray does
# not immediately re-hit the triangle it left, small enough not to tunnel
# through a neighbouring panel.
_RAY_EPSILON = 1e-4


def _hammersley(index: int, count: int) -> tuple[float, float]:
    """A fixed low-discrepancy pair. Deterministic, and never a random seed.

    Randomised sampling would make two measurements of unchanged geometry
    disagree slightly, which is exactly the signal a correction is judged by.
    """
    bits = index
    reversed_bits = 0.0
    fraction = 0.5
    while bits:
        reversed_bits += (bits & 1) * fraction
        bits >>= 1
        fraction *= 0.5
    return (index + 0.5) / count, reversed_bits


def _triangles(objects: list) -> tuple[list[dict[str, Any]], list[Vector], list[tuple[int, int, int]]]:
    """Every subject's geometry in world space, triangulated once.

    One combined BVH rather than one per object, because a part hidden behind
    another part is genuinely not observed and a per-object test would report it
    visible.
    """
    faces: list[dict[str, Any]] = []
    points: list[Vector] = []
    indices: list[tuple[int, int, int]] = []

    for obj in objects:
        matrix = obj.matrix_world
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            bm.faces.ensure_lookup_table()
            for face in bm.faces:
                base = len(points)
                corners = [matrix @ vert.co for vert in face.verts]
                if len(corners) != 3:
                    continue
                points.extend(corners)
                indices.append((base, base + 1, base + 2))
                edge_a = corners[1] - corners[0]
                edge_b = corners[2] - corners[0]
                normal = edge_a.cross(edge_b)
                area = normal.length / 2.0
                faces.append({
                    "object": object_id(obj),
                    "corners": corners,
                    "normal": normal.normalized() if normal.length > 1e-12 else Vector((0, 0, 1)),
                    "area": area,
                    # Which authored face this triangle came from, so an
                    # unobserved region can be reported in terms an agent can
                    # act on rather than as triangle soup.
                    "source_face": face.index,
                })
        finally:
            bm.free()
    return faces, points, indices


def _samples(faces: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Area-weighted surface points, distributed deterministically.

    Every triangle gets at least one sample so a small but important face cannot
    vanish from the measurement entirely; the rest of the budget follows area,
    because a coverage *fraction* that counted triangles equally would be
    dominated by wherever the mesh happens to be dense.
    """
    total_area = sum(face["area"] for face in faces) or 1.0
    samples: list[dict[str, Any]] = []
    for face_index, face in enumerate(faces):
        share = face["area"] / total_area
        count = max(1, int(round(share * budget)))
        weight = face["area"] / count
        a, b, c = face["corners"]
        for step in range(count):
            u, v = _hammersley(step, count)
            # Fold the unit square onto the triangle so the points stay inside it.
            if u + v > 1.0:
                u, v = 1.0 - u, 1.0 - v
            samples.append({
                "position": a + (b - a) * u + (c - a) * v,
                "normal": face["normal"],
                "area": weight,
                "face": face_index,
                "object": face["object"],
                "source_face": face["source_face"],
            })
    return samples


def _visible(tree: BVHTree, sample: dict[str, Any], camera: dict[str, Any],
             far: float) -> bool:
    """Front-facing and unoccluded, which are two different questions.

    Facing alone would call the inside of a hollow shell observed. Occlusion
    alone would call a back face observed because nothing blocked the ray to it.
    """
    if camera["projection"] == canonical_views.ORTHOGRAPHIC:
        # The camera sits along +direction from the subject centre, so the way
        # back to it from any surface point is that same direction. Parallel
        # projection means it does not depend on where the point is.
        toward = Vector(camera["direction"])
        distance = far
    else:
        toward = Vector(camera["position"]) - sample["position"]
        distance = toward.length
        if distance < 1e-9:
            return False
        toward = toward / distance

    if sample["normal"].dot(toward) <= 1e-6:
        return False
    origin = sample["position"] + sample["normal"] * _RAY_EPSILON
    hit = tree.ray_cast(origin, toward, distance)
    return hit[0] is None


def measure(objects: list, *, level: int, projection: str, width: int, height: int,
            budget: int, selected: list[str] | None, min_coverage: float | None,
            runtime) -> Measurement:
    """Coverage from a chosen view set, and the best camera to add next."""
    from .certificate import pins_for

    faces, points, indices = _triangles(objects)
    measurement = Measurement("coverage", {})
    if not faces:
        measurement.metric("coverage.observed_fraction", 0.0,
                           direction=HIGHER_BETTER, unit="fraction")
        measurement.unmeasured("coverage.meets_declared_minimum", "no faces to observe")
        measurement.pins = pins_for(runtime, [])
        return measurement

    corners = [corner for face in faces for corner in face["corners"]]
    frame = canonical_views.subject_frame(corners)
    cameras = canonical_views.camera_set(frame, level=level, projection=projection,
                                         width=width, height=height)
    by_id = {camera["view"]: camera for camera in cameras}
    if selected is None:
        chosen = list(cameras)
    else:
        missing = [name for name in selected if name not in by_id]
        if missing:
            raise ValueError(f"unknown view ids for this sampler: {missing}")
        chosen = [by_id[name] for name in selected]

    tree = BVHTree.FromPolygons(points, indices, all_triangles=True)
    samples = _samples(faces, budget)
    total_area = sum(sample["area"] for sample in samples)
    far = float(frame["radius"]) * 12.0

    # Which candidate sees which sample, computed once for every camera in the
    # sampler. It is what makes the next-view recommendation a calculation
    # rather than a guess, and it costs nothing extra to keep.
    seen_by: dict[str, set[int]] = {}
    for camera in cameras:
        seen_by[camera["view"]] = {
            index for index, sample in enumerate(samples)
            if _visible(tree, sample, camera, far)
        }

    observed: set[int] = set()
    per_view = []
    for camera in chosen:
        new = seen_by[camera["view"]] - observed
        observed |= new
        per_view.append({
            "view": camera["view"],
            "direction": camera["direction"],
            "newly_observed_area": round(sum(samples[i]["area"] for i in new), 9),
            "newly_observed_samples": len(new),
            "cumulative_observed_fraction": round(
                sum(samples[i]["area"] for i in observed) / total_area, 6),
        })

    observed_area = sum(samples[index]["area"] for index in observed)
    unobserved = set(range(len(samples))) - observed
    regions = _regions(samples, unobserved, faces)
    _record(measurement, observed_area, total_area, len(chosen), regions, min_coverage)

    measurement.subjects = [{
        "object": obj_id,
        "mesh_revision": None,
    } for obj_id in sorted({face["object"] for face in faces})]
    for subject in measurement.subjects:
        for obj in objects:
            if object_id(obj) == subject["object"]:
                subject["mesh_revision"] = mesh_revision(obj)

    measurement.notes.append(
        "visibility is geometric: front-facing plus an unoccluded ray to the camera, "
        "cast against every subject together so one part occluding another counts")
    measurement.unmeasured(
        "coverage.continuous_surface",
        f"coverage is estimated from {len(samples)} area-weighted surface samples, "
        f"not from continuous surface integration")

    measurement.pins = pins_for(runtime, measurement.subjects)
    measurement.pins["views"] = {
        "sampler": canonical_views.sampler_id(level, projection,
                                              canonical_views.FRAMING_MARGIN),
        "schema": COVERAGE_SCHEMA,
        "level": level,
        "candidate_count": len(cameras),
        "projection": projection,
        "width": width,
        "height": height,
        "frame": frame,
        "selected": [camera["view"] for camera in chosen],
    }
    measurement.subjects.append({
        "object": "__views__",
        "mesh_revision": None,
        "cameras": chosen,
        "per_view": per_view,
    })
    if regions:
        measurement.subjects.append({
            "object": "__unobserved__",
            "mesh_revision": None,
            "regions": regions[:16],
        })

    recommendation = next_view(seen_by, observed, samples, by_id, chosen)
    if recommendation is not None:
        measurement.subjects.append({"object": "__next_view__", "mesh_revision": None,
                                     **recommendation})
    return measurement


