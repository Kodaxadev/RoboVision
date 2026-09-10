"""Where a verification camera goes, decided by the asset rather than by a human.

A viewport that happened to be open is not an observation contract. If the
canonical views depend on where somebody left the view rotated, two measurements
of the same asset are not comparable and the whole Q0/Q1 mechanic is built on
sand. So the views here are derived from the subject's own bounds, ordered
deterministically, and identified by a sampler id that changes when anything
about the construction changes.

The graphics context is not part of the definition. These cameras are pure
geometry — a position, a direction, an up vector and a projection — computed
identically in a background Blender that cannot render at all. That is what lets
surface coverage be measured by raycasting and silhouettes be rendered from the
*same* cameras later, with one shared provenance record tying the two kinds of
evidence to the same viewpoint.

Directions come from a geodesic subdivision of an icosahedron rather than a
latitude/longitude grid. A lat/long grid piles samples at the poles and starves
the equator, so a "uniform" set of that shape would inspect the top of an asset
several times over while missing its sides.
"""
from __future__ import annotations

import hashlib
from typing import Any

from mathutils import Matrix, Vector

from ..recipe import canonical

VIEWS_SCHEMA = 1

# Vertex counts are 10 * 4^level + 2, which is where the usable sizes come from.
ICOSPHERE_LEVELS = {0: 12, 1: 42, 2: 162}

ORTHOGRAPHIC = "orthographic"
PERSPECTIVE = "perspective"
PROJECTIONS = (ORTHOGRAPHIC, PERSPECTIVE)

# Room around the subject so it is never clipped by its own framing. Small and
# fixed: a margin that varied with the asset would make two measurements of
# different assets incomparable in a way nothing in the record would show.
FRAMING_MARGIN = 1.08


def _icosahedron() -> tuple[list[Vector], list[tuple[int, int, int]]]:
    phi = (1.0 + 5.0 ** 0.5) / 2.0
    raw = [
        (-1, phi, 0), (1, phi, 0), (-1, -phi, 0), (1, -phi, 0),
        (0, -1, phi), (0, 1, phi), (0, -1, -phi), (0, 1, -phi),
        (phi, 0, -1), (phi, 0, 1), (-phi, 0, -1), (-phi, 0, 1),
    ]
    verts = [Vector(v).normalized() for v in raw]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    return verts, faces


def _subdivide(verts: list[Vector], faces: list[tuple[int, int, int]]):
    """One geodesic subdivision step, with midpoints shared between neighbours."""
    midpoints: dict[tuple[int, int], int] = {}

    def midpoint(a: int, b: int) -> int:
        key = (a, b) if a < b else (b, a)
        if key not in midpoints:
            verts.append(((verts[a] + verts[b]) / 2.0).normalized())
            midpoints[key] = len(verts) - 1
        return midpoints[key]

    out: list[tuple[int, int, int]] = []
    for a, b, c in faces:
        ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
        out.extend([(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)])
    return verts, out


def directions(level: int) -> list[Vector]:
    """The sampler's unit directions, in one stable order.

    Sorted by rounded coordinates rather than left in subdivision order, because
    subdivision order is an implementation detail and the camera *ids* derived
    from this list have to survive a refactor of the loop above.
    """
    if level not in ICOSPHERE_LEVELS:
        raise ValueError(f"unsupported icosphere level: {level}")
    verts, faces = _icosahedron()
    for _ in range(level):
        verts, faces = _subdivide(verts, faces)
    unique: dict[tuple[float, float, float], Vector] = {}
    for vert in verts:
        key = (round(vert.x, 9) + 0.0, round(vert.y, 9) + 0.0, round(vert.z, 9) + 0.0)
        unique.setdefault(key, Vector(key).normalized())
    return [unique[key] for key in sorted(unique)]


def sampler_id(level: int, projection: str, margin: float) -> str:
    """An identity for the whole camera construction, not just its size.

    Anything that changes where the cameras end up changes this string, so a
    coverage number can never be compared against one produced by a different
    construction that happens to have the same view count.
    """
    body = canonical({
        "schema": VIEWS_SCHEMA, "kind": "icosphere", "level": level,
        "projection": projection, "margin": round(margin, 6),
        "count": ICOSPHERE_LEVELS[level],
    })
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return f"rvviews:icosphere-l{level}-{projection}:{digest}"


def subject_frame(corners: list[Vector]) -> dict[str, Any]:
    """The asset-relative space every canonical camera is placed in.

    Derived from the subject's own world bounds, so the same asset yields the
    same frame however the file was navigated. The radius is of the bounding
    *sphere* rather than the longest axis: framing on one axis would clip a long
    thin asset viewed down its length.
    """
    minimum = Vector((min(c.x for c in corners), min(c.y for c in corners),
                      min(c.z for c in corners)))
    maximum = Vector((max(c.x for c in corners), max(c.y for c in corners),
                      max(c.z for c in corners)))
    center = (minimum + maximum) / 2.0
    radius = max((corner - center).length for corner in corners)
    return {
        "center": [round(v, 9) for v in center],
        "min": [round(v, 9) for v in minimum],
        "max": [round(v, 9) for v in maximum],
        "size": [round(v, 9) for v in (maximum - minimum)],
        # A degenerate subject would otherwise put every camera in one place and
        # divide by zero doing it.
        "radius": round(max(radius, 1e-6), 9),
    }


def _look_at(position: Vector, target: Vector) -> tuple[Matrix, Vector]:
    """A view matrix and the up vector it was built from, chosen deterministically.

    +Z is the up reference except where the direction is nearly parallel to it,
    which is the one case where the cross product collapses. Choosing the
    fallback by a fixed rule rather than by whatever is convenient keeps the
    top-down and bottom-up cameras reproducible.
    """
    forward = (target - position)
    if forward.length < 1e-9:
        forward = Vector((0.0, 0.0, -1.0))
    forward.normalize()
    reference = Vector((0.0, 0.0, 1.0))
    if abs(forward.dot(reference)) > 0.999:
        reference = Vector((0.0, 1.0, 0.0))
    right = forward.cross(reference).normalized()
    up = right.cross(forward).normalized()
    # Blender convention: the camera looks down its own -Z.
    rotation = Matrix((
        (right.x, up.x, -forward.x, 0.0),
        (right.y, up.y, -forward.y, 0.0),
        (right.z, up.z, -forward.z, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    )).transposed()
    translation = Matrix.Translation(-position)
    return rotation @ translation, up


def _projection(projection: str, radius: float, aspect: float,
                near: float, far: float, fov: float) -> Matrix:
    if projection == ORTHOGRAPHIC:
        half = radius * FRAMING_MARGIN
        return Matrix((
            (1.0 / (half * aspect), 0.0, 0.0, 0.0),
            (0.0, 1.0 / half, 0.0, 0.0),
            (0.0, 0.0, -2.0 / (far - near), -(far + near) / (far - near)),
            (0.0, 0.0, 0.0, 1.0),
        ))
    from math import tan

    focal = 1.0 / tan(fov / 2.0)
    return Matrix((
        (focal / aspect, 0.0, 0.0, 0.0),
        (0.0, focal, 0.0, 0.0),
        (0.0, 0.0, (far + near) / (near - far), 2.0 * far * near / (near - far)),
        (0.0, 0.0, -1.0, 0.0),
    ))


def camera_set(frame: dict[str, Any], *, level: int, projection: str,
               width: int, height: int, fov: float = 0.6981317) -> list[dict[str, Any]]:
    """Every canonical camera, with the provenance a render would need.

    The matrices are computed here rather than by whatever draws later, so a
    silhouette rendered from view 17 and a coverage raycast from view 17 are
    provably the same viewpoint instead of two independent constructions that
    agree by convention.
    """
    if projection not in PROJECTIONS:
        raise ValueError(f"unsupported projection: {projection}")
    center = Vector(frame["center"])
    radius = float(frame["radius"])
    aspect = width / height

    if projection == ORTHOGRAPHIC:
        distance = radius * 4.0
        near, far = max(1e-4, distance - radius * 3.0), distance + radius * 3.0
    else:
        from math import sin

        distance = (radius * FRAMING_MARGIN) / max(sin(fov / 2.0), 1e-6)
        near, far = max(1e-4, distance - radius * 2.0), distance + radius * 2.0

    cameras = []
    for index, direction in enumerate(directions(level)):
        position = center + direction * distance
        view, up = _look_at(position, center)
        cameras.append({
            "view": f"v{index:03d}",
            "index": index,
            "direction": [round(v, 9) for v in direction],
            "position": [round(v, 9) for v in position],
            "target": frame["center"],
            "up": [round(v, 9) for v in up],
            "projection": projection,
            "orthographic_extent": round(radius * FRAMING_MARGIN, 9)
            if projection == ORTHOGRAPHIC else None,
            "field_of_view": round(fov, 9) if projection == PERSPECTIVE else None,
            "near_clip": round(near, 9),
            "far_clip": round(far, 9),
            "width": width,
            "height": height,
            "view_matrix": [[round(v, 9) for v in row] for row in view],
            "projection_matrix": [
                [round(v, 9) for v in row]
                for row in _projection(projection, radius, aspect, near, far, fov)
            ],
        })
    return cameras
