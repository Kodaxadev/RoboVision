"""What is measurably wrong with the geometry, and what is measurably fine.

Deliberately narrow. These are the properties a first Artist Loop experiment has
to be able to gate on or aim at: geometry that is not valid at all, normals that
disagree with each other or with the outside, parts that are not attached to
anything, holes, and surfaces passing through one another. Not a metric library —
UVs, bakes, retopology, part graphs and the detail spectrum are all deliberately
absent, and adding them before the first closed loop runs would be building
measurement for its own sake.

The one judgement call encoded here is **intentional-open semantics**. An open
boundary is not a defect by itself; a plane, a cloth panel and a cut-away section
are all supposed to have one. So the caller declares which subjects are meant to
be open, and the invariant is about *unintended* openness. A validator that
called every boundary edge an error would be rejected by its users within a day,
and a loop that trusted it would spend its budget welding holes that belong there.

Where something cannot be established it is recorded as a limit rather than
passed. A self-intersection test that was truncated must not read like a mesh
that has none.
"""
from __future__ import annotations

from collections import deque
from typing import Any

import bmesh
from mathutils.bvhtree import BVHTree

from ..identity import mesh_revision, object_id
from ..registry import HostError
from .certificate import HIGHER_BETTER, LOWER_BETTER, NEUTRAL, Measurement

# A face pair that shares a vertex touches by construction and is not an
# intersection. Overlap testing reports those, so they are filtered rather than
# counted, or every closed mesh would look self-intersecting.
_INTERSECTION_PAIR_CAP = 4096


def _bmesh_for(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    return bm


def _connected_components(bm) -> list[set]:
    """Vertex sets, one per island. Floating parts are the interesting output."""
    unseen = set(bm.verts)
    components: list[set] = []
    while unseen:
        seed = min(unseen, key=lambda vert: vert.index)
        unseen.remove(seed)
        queue = deque([seed])
        component = set()
        while queue:
            vert = queue.popleft()
            component.add(vert)
            for edge in vert.link_edges:
                other = edge.other_vert(vert)
                if other in unseen:
                    unseen.remove(other)
                    queue.append(other)
        components.append(component)
    components.sort(key=len, reverse=True)
    return components


def _boundary_loops(bm) -> list[list[int]]:
    """Closed rings of boundary edges — holes, counted as holes rather than edges.

    An agent asked to close a hole cares how many holes there are, not how many
    edges they are made of. Reporting 68 boundary edges where there are two holes
    is a number that cannot be aimed at.
    """
    remaining = {edge for edge in bm.edges if edge.is_boundary}
    loops: list[list[int]] = []
    while remaining:
        start = min(remaining, key=lambda edge: edge.index)
        remaining.remove(start)
        loop = [start.index]
        queue = deque([start])
        while queue:
            edge = queue.popleft()
            for vert in edge.verts:
                for neighbour in vert.link_edges:
                    if neighbour in remaining:
                        remaining.remove(neighbour)
                        loop.append(neighbour.index)
                        queue.append(neighbour)
        loops.append(sorted(loop))
    return loops


def _inconsistent_windings(bm) -> list[int]:
    """Edges whose two faces disagree about which way is out.

    A manifold edge is consistently wound when its two faces traverse it in
    opposite directions. If both loops start at the same vertex the faces are
    wound the same way, which is exactly what a flipped normal looks like from
    the topology rather than from a dot product with a guessed viewpoint.
    """
    inconsistent: list[int] = []
    for edge in bm.edges:
        if len(edge.link_faces) != 2:
            continue
        first, second = (loop for loop in edge.link_loops)
        if first.vert == second.vert:
            inconsistent.append(edge.index)
    return inconsistent


def _self_intersections(bm, obj) -> tuple[int, bool]:
    """Face pairs that pass through each other, ignoring ones that merely touch.

    Returns the count and whether the search was complete. Adjacency is the whole
    difficulty: two faces sharing a vertex overlap by construction, so filtering
    them is what separates a real intersection from the mesh being connected.
    """
    tree = BVHTree.FromBMesh(bm)
    pairs = tree.overlap(tree)
    count = 0
    complete = True
    for first, second in pairs:
        if first >= second:
            continue
        if first >= len(bm.faces) or second >= len(bm.faces):
            continue
        a, b = bm.faces[first], bm.faces[second]
        if set(a.verts) & set(b.verts):
            continue
        count += 1
        if count >= _INTERSECTION_PAIR_CAP:
            complete = False
            break
    del obj
    return count, complete


# How a surface's boundary should be read. Deliberately not a validity verdict:
# `edge.is_manifold == False` means "boundary or worse", and treating that as
# invalid geometry is what makes an intentionally open surface impossible to
# pass. Openness is a shape fact plus a declaration; non-manifoldness is a
# separate structural claim with its own invariant.
CLOSED = "closed"
INTENTIONALLY_OPEN = "intentionally_open"
UNEXPECTEDLY_OPEN = "unexpectedly_open"


def _openness(loops: list[list[int]], declared_open: bool) -> str:
    if not loops:
        return CLOSED
    return INTENTIONALLY_OPEN if declared_open else UNEXPECTEDLY_OPEN


def _subject(obj, bm) -> dict[str, Any]:
    return {
        "object": object_id(obj),
        "name": obj.name,
        "mesh": obj.data.name_full,
        "mesh_revision": mesh_revision(obj),
        "counts": {"vertices": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces)},
    }


def measure(objects: list, *, intentional_open: set[str], epsilon: float,
            check_intersections: bool, subject_set: str | None,
            runtime) -> Measurement:
    """Measure every subject, then report the totals a correction is judged on.

    Totals rather than per-object numbers because a correction is accepted or
    rejected once. Per-object detail stays in `subjects` for a loop that needs to
    know where the problem is; the metric is what decides.
    """
    from .certificate import pins_for

    measurement = Measurement("geometry", {})
    totals = {
        "non_manifold_edges": 0, "non_manifold_vertices": 0,
        "degenerate_faces": 0, "zero_length_edges": 0,
        "loose_vertices": 0, "wire_edges": 0, "ngons": 0, "boundary_loops": 0,
        "unintended_boundary_loops": 0, "components": 0, "floating_components": 0,
        "inconsistent_normal_edges": 0, "self_intersecting_face_pairs": 0,
        "inverted_solids": 0, "faces": 0, "triangles": 0, "quads": 0,
    }
    intersections_complete = True
    intersections_attempted = False

    for obj in objects:
        bm = _bmesh_for(obj)
        try:
            subject = _subject(obj, bm)
            declared_open = subject["object"] in intentional_open or obj.name in intentional_open

            # Deliberately *not* `edge.is_manifold`, which is also false for a
            # boundary edge. Conflating the two makes an intentionally open
            # surface permanently non-manifold, so a cloth panel or a cut-away
            # section could never pass — measured, and it is why these are two
            # invariants rather than one. Non-manifold here means topology no
            # surface can have: an edge shared by three or more faces, or a
            # vertex whose faces do not form a single fan. Openness is the
            # boundary invariant's business and nothing else's.
            non_manifold = [e.index for e in bm.edges if len(e.link_faces) > 2]
            non_manifold_verts = [
                v.index for v in bm.verts
                if v.link_faces and not v.is_manifold and not v.is_boundary
            ]
            zero_edges = [e.index for e in bm.edges if e.calc_length() <= epsilon]
            degenerate = [f.index for f in bm.faces if f.calc_area() <= epsilon]
            loose = [v.index for v in bm.verts if not v.link_edges]
            wire = [e.index for e in bm.edges if e.is_wire]
            ngons = [f.index for f in bm.faces if len(f.verts) > 4]
            loops = _boundary_loops(bm)
            components = _connected_components(bm)
            windings = _inconsistent_windings(bm)

            closed = not loops and not non_manifold
            # A signed volume is only meaningful for a closed, consistently wound
            # solid. Asking it of an open surface would produce a confident number
            # about a quantity that does not exist there.
            volume = bm.calc_volume(signed=True) if closed and not windings else None
            inverted = bool(closed and not windings and volume is not None and volume < 0.0)

            pairs, complete = (0, True)
            if check_intersections and bm.faces:
                intersections_attempted = True
                pairs, complete = _self_intersections(bm, obj)
                intersections_complete = intersections_complete and complete

            subject.update({
                "intentional_open": declared_open,
                "closed": closed,
                # The classification a caller actually wants, rather than one it
                # has to reconstruct from two metrics and a declaration. Openness
                # and non-manifoldness are orthogonal here on purpose: a surface
                # can be legitimately open *and* structurally broken, and an
                # agent told only "invalid" cannot tell which of the two to fix.
                "openness": _openness(loops, declared_open),
                "non_manifold": bool(non_manifold or non_manifold_verts),
                "signed_volume": volume,
                "boundary_loops": len(loops),
                "components": len(components),
                "issues": {
                    "non_manifold_edges": non_manifold[:64],
                    "non_manifold_vertices": non_manifold_verts[:64],
                    "zero_length_edges": zero_edges[:64],
                    "degenerate_faces": degenerate[:64],
                    "loose_vertices": loose[:64],
                    "wire_edges": wire[:64],
                    "inconsistent_normal_edges": windings[:64],
                },
            })
            measurement.subjects.append(subject)

            totals["non_manifold_edges"] += len(non_manifold)
            totals["non_manifold_vertices"] += len(non_manifold_verts)
            totals["degenerate_faces"] += len(degenerate)
            totals["zero_length_edges"] += len(zero_edges)
            totals["loose_vertices"] += len(loose)
            totals["wire_edges"] += len(wire)
            totals["ngons"] += len(ngons)
            totals["boundary_loops"] += len(loops)
            totals["unintended_boundary_loops"] += 0 if declared_open else len(loops)
            totals["components"] += len(components)
            totals["floating_components"] += max(0, len(components) - 1)
            totals["inconsistent_normal_edges"] += len(windings)
            totals["self_intersecting_face_pairs"] += pairs
            totals["inverted_solids"] += 1 if inverted else 0
            totals["faces"] += len(bm.faces)
            totals["triangles"] += sum(1 for f in bm.faces if len(f.verts) == 3)
            totals["quads"] += sum(1 for f in bm.faces if len(f.verts) == 4)
        finally:
            bm.free()

    _record(measurement, totals)

    if not check_intersections:
        measurement.unmeasured("geometry.no_self_intersection", "not_requested")
    elif not intersections_attempted:
        measurement.unmeasured("geometry.no_self_intersection", "no_faces_to_test")
    elif not intersections_complete:
        measurement.unmeasured(
            "geometry.no_self_intersection",
            f"overlap search truncated at {_INTERSECTION_PAIR_CAP} pairs")

    measurement.pins = pins_for(runtime, measurement.subjects, subject_set)
    return measurement


def _record(measurement: Measurement, totals: dict[str, int]) -> None:
    """Turn the totals into the metrics and the gates, which are different things.

    A metric is something a correction may aim at or be protected on; an
    invariant is something it may not break whatever it achieved. Keeping them
    apart is what stops a loop trading correctness for its own objective.
    """
    lower = ("non_manifold_edges", "non_manifold_vertices",
             "degenerate_faces", "zero_length_edges",
             "loose_vertices", "wire_edges", "boundary_loops",
             "unintended_boundary_loops", "floating_components",
             "inconsistent_normal_edges", "self_intersecting_face_pairs",
             "inverted_solids")
    for name in lower:
        measurement.metric("geometry." + name, totals[name], direction=LOWER_BETTER)
    for name in ("components", "faces", "triangles", "quads", "ngons"):
        measurement.metric("geometry." + name, totals[name], direction=NEUTRAL)
    # The only one that is better higher: a face budget spent on quads rather
    # than triangles is the shape most downstream tools prefer, and it is a thing
    # a correction can legitimately be asked to improve.
    quad_share = totals["quads"] / totals["faces"] if totals["faces"] else 0.0
    measurement.metric("geometry.quad_share", round(quad_share, 6),
                       direction=HIGHER_BETTER, unit="fraction")

    # Manifold and closed are separate claims. This one is about topology no
    # surface can have; whether the surface has a hole is the open-boundary
    # invariant, which the caller can declare intentional.
    measurement.invariant("geometry.manifold",
                          totals["non_manifold_edges"] == 0
                          and totals["non_manifold_vertices"] == 0,
                          non_manifold_edges=totals["non_manifold_edges"],
                          non_manifold_vertices=totals["non_manifold_vertices"])
    measurement.invariant("geometry.no_degenerate_faces", totals["degenerate_faces"] == 0,
                          degenerate_faces=totals["degenerate_faces"])
    measurement.invariant("geometry.no_zero_length_edges", totals["zero_length_edges"] == 0,
                          zero_length_edges=totals["zero_length_edges"])
    measurement.invariant("geometry.no_loose_geometry",
                          totals["loose_vertices"] == 0 and totals["wire_edges"] == 0,
                          loose_vertices=totals["loose_vertices"],
                          wire_edges=totals["wire_edges"])
    measurement.invariant("geometry.normals_consistent",
                          totals["inconsistent_normal_edges"] == 0,
                          inconsistent_normal_edges=totals["inconsistent_normal_edges"])
    measurement.invariant("geometry.normals_outward", totals["inverted_solids"] == 0,
                          inverted_solids=totals["inverted_solids"])
    # The declared-intent one. Openness itself is not a defect and is reported as
    # a metric; only openness nobody asked for is a gate.
    measurement.invariant("geometry.no_unintended_open_boundaries",
                          totals["unintended_boundary_loops"] == 0,
                          unintended_boundary_loops=totals["unintended_boundary_loops"],
                          boundary_loops=totals["boundary_loops"])
    measurement.invariant("geometry.no_floating_components",
                          totals["floating_components"] == 0,
                          floating_components=totals["floating_components"])
    measurement.invariant("geometry.no_self_intersection",
                          totals["self_intersecting_face_pairs"] == 0,
                          self_intersecting_face_pairs=totals["self_intersecting_face_pairs"])


def resolve_subjects(params: dict[str, Any]) -> tuple[list, set[str]]:
    """Which objects are being measured, and which are allowed to be open."""
    from ..identity import resolve_object

    refs = params.get("objects")
    if refs is None:
        single = params.get("object")
        refs = [single] if single is not None else None
    if not isinstance(refs, list) or not refs:
        raise HostError("INVALID_PARAMS", "object or objects is required")

    objects = []
    for ref in refs:
        obj = resolve_object(ref)
        if obj.type != "MESH" or obj.data is None:
            raise HostError("INVALID_PARAMS", f"{obj.name} is not a mesh",
                            data={"object": object_id(obj), "type": obj.type})
        objects.append(obj)

    declared = params.get("intentional_open", [])
    if not isinstance(declared, list) or any(not isinstance(item, str) for item in declared):
        raise HostError("INVALID_PARAMS", "intentional_open must be an array of strings")
    return objects, set(declared)
