"""Geometry truth, measured against real damage rather than fixtures.

Every defect below is built by actually damaging a mesh, because a validator
proved against a hand-written expectation is only proved against the author's
idea of the defect. A cube with a face removed really is open; a cube with a face
flipped really is inconsistently wound; two boxes really do pass through each
other.

The judgement call under test is intentional-open semantics. An open boundary is
not a defect by itself — a plane, a cloth panel and a cut-away section are all
supposed to have one — so the caller declares which subjects are meant to be
open, and only openness nobody asked for is a gate. A validator that called every
boundary edge an error would be turned off within a day, and a loop that trusted
it would spend its whole budget welding holes that belong there.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bmesh
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from truth_support import cube, edit, failing, metrics  # noqa: E402

FINDINGS: dict[str, object] = {}


def clean_geometry_measures_clean(rv: Host) -> None:
    clean_scene()
    cube(rv, "Clean")
    certificate = rv.result("truth.geometry", {"object": "Clean"})

    FINDINGS["clean_failing"] = failing(certificate)
    FINDINGS["clean_holds"] = certificate["invariants_hold"]
    FINDINGS["clean_limits"] = sorted(certificate["limits"])
    values = metrics(certificate)
    FINDINGS["clean_boundary_loops"] = values["geometry.boundary_loops"]
    FINDINGS["clean_components"] = values["geometry.components"]
    FINDINGS["clean_quad_share"] = values["geometry.quad_share"]
    FINDINGS["clean_pins"] = sorted(certificate["pins"])
    FINDINGS["clean_openness"] = certificate["subjects"][0]["openness"]


def a_hole_is_a_hole_and_not_an_edge_count(rv: Host) -> None:
    """A loop asked to close a hole needs the number of holes, not of edges."""
    clean_scene()
    cube(rv, "Holed")
    edit("Holed", lambda bm: bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY"))

    certificate = rv.result("truth.geometry", {"object": "Holed"})
    values = metrics(certificate)
    FINDINGS["holed_loops"] = values["geometry.boundary_loops"]
    FINDINGS["holed_unintended"] = values["geometry.unintended_boundary_loops"]
    FINDINGS["holed_failing"] = failing(certificate)
    FINDINGS["holed_closed"] = certificate["subjects"][0]["closed"]
    FINDINGS["holed_openness"] = certificate["subjects"][0]["openness"]
    FINDINGS["holed_non_manifold"] = certificate["subjects"][0]["non_manifold"]

    # Declared open, the same geometry is not a defect. The metric still reports
    # the hole; only the gate changes, because openness is a fact and unintended
    # openness is a judgement the caller owns.
    declared = rv.result("truth.geometry",
                         {"object": "Holed", "intentional_open": ["Holed"]})
    FINDINGS["declared_loops"] = metrics(declared)["geometry.boundary_loops"]
    FINDINGS["declared_unintended"] = metrics(declared)["geometry.unintended_boundary_loops"]
    FINDINGS["declared_failing"] = failing(declared)
    FINDINGS["declared_openness"] = declared["subjects"][0]["openness"]


def a_flipped_face_is_found_without_guessing_a_viewpoint(rv: Host) -> None:
    clean_scene()
    cube(rv, "Flipped")
    edit("Flipped", lambda bm: bm.faces[2].normal_flip())

    certificate = rv.result("truth.geometry", {"object": "Flipped"})
    FINDINGS["flipped_edges"] = metrics(certificate)["geometry.inconsistent_normal_edges"]
    FINDINGS["flipped_failing"] = failing(certificate)


def an_inside_out_solid_is_distinguished_from_an_inconsistent_one(rv: Host) -> None:
    """Consistently wound and entirely inward is a different defect, and a real one."""
    clean_scene()
    cube(rv, "Inverted")
    edit("Inverted", lambda bm: bmesh.ops.reverse_faces(bm, faces=bm.faces[:]))

    certificate = rv.result("truth.geometry", {"object": "Inverted"})
    values = metrics(certificate)
    FINDINGS["inverted_consistent"] = values["geometry.inconsistent_normal_edges"]
    FINDINGS["inverted_solids"] = values["geometry.inverted_solids"]
    FINDINGS["inverted_failing"] = failing(certificate)
    FINDINGS["inverted_volume"] = certificate["subjects"][0]["signed_volume"]


def a_floating_part_is_counted_as_a_part(rv: Host) -> None:
    clean_scene()
    cube(rv, "TwoParts")
    edit("TwoParts", lambda bm: bmesh.ops.create_cube(
        bm, size=0.5, matrix=Matrix.Translation((4.0, 0.0, 0.0))))

    certificate = rv.result("truth.geometry", {"object": "TwoParts"})
    values = metrics(certificate)
    FINDINGS["parts_components"] = values["geometry.components"]
    FINDINGS["parts_floating"] = values["geometry.floating_components"]
    FINDINGS["parts_failing"] = failing(certificate)


def surfaces_passing_through_each_other_are_found(rv: Host) -> None:
    """Two boxes sharing space, with nothing in common to explain the overlap away."""
    clean_scene()
    cube(rv, "Crossed")
    edit("Crossed", lambda bm: bmesh.ops.create_cube(
        bm, size=0.6, matrix=Matrix.Translation((0.3, 0.3, 0.3))))

    certificate = rv.result("truth.geometry", {"object": "Crossed"})
    FINDINGS["crossed_pairs"] = metrics(certificate)["geometry.self_intersecting_face_pairs"]
    FINDINGS["crossed_failing"] = failing(certificate)

    # Opted out, the answer is "not measured" rather than "none found". An
    # unchecked property reported as passing is the failure the certificate shape
    # exists to prevent.
    skipped = rv.result("truth.geometry", {"object": "Crossed", "check_intersections": False})
    FINDINGS["skipped_limits"] = sorted(skipped["limits"])
    FINDINGS["skipped_pairs"] = metrics(skipped)["geometry.self_intersecting_face_pairs"]


def degenerate_geometry_is_found(rv: Host) -> None:
    clean_scene()
    cube(rv, "Degenerate")

    def collapse(bm):
        # One face's vertices all in the same place: a zero-area face and the
        # zero-length edges around it, which is what a botched merge leaves
        # behind. Measured first: collapsing a *single* vertex produces zero
        # length edges and no degenerate face at all, because the quads either
        # side merely become triangles — so a gate written around that would
        # have passed a mesh with a real hole in its topology.
        bm.faces.ensure_lookup_table()
        target = bm.faces[0].verts[0].co.copy()
        for vert in bm.faces[0].verts:
            vert.co = target.copy()

    edit("Degenerate", collapse)
    certificate = rv.result("truth.geometry", {"object": "Degenerate"})
    values = metrics(certificate)
    FINDINGS["degenerate_zero_edges"] = values["geometry.zero_length_edges"]
    FINDINGS["degenerate_faces"] = values["geometry.degenerate_faces"]
    FINDINGS["degenerate_failing"] = failing(certificate)


def main() -> None:
    rv = Host("truth-geometry")
    clean_geometry_measures_clean(rv)
    a_hole_is_a_hole_and_not_an_edge_count(rv)
    a_flipped_face_is_found_without_guessing_a_viewpoint(rv)
    an_inside_out_solid_is_distinguished_from_an_inconsistent_one(rv)
    a_floating_part_is_counted_as_a_part(rv)
    surfaces_passing_through_each_other_are_found(rv)
    degenerate_geometry_is_found(rv)

    expect(FINDINGS["clean_failing"] == [],
           f"a clean cube failed an invariant: {FINDINGS['clean_failing']}")
    expect(FINDINGS["clean_holds"] is True, "a clean cube did not hold its invariants")
    expect(FINDINGS["clean_limits"] == [],
           f"a full measurement left something unmeasured: {FINDINGS['clean_limits']}")
    expect(FINDINGS["clean_boundary_loops"] == 0, "a closed cube reported a hole")
    expect(FINDINGS["clean_openness"] == "closed", "a closed cube was not classified as closed")
    expect(FINDINGS["clean_components"] == 1, "a cube reported more than one part")
    expect(FINDINGS["clean_quad_share"] == 1.0, "an all-quad cube did not measure as one")
    for pin in ("world_incarnation", "coordinate_contract", "revision", "fingerprint"):
        expect(pin in FINDINGS["clean_pins"], f"a certificate is not pinned to {pin}")

    expect(FINDINGS["holed_loops"] == 1,
           f"one hole was not counted as one hole: {FINDINGS['holed_loops']}")
    expect(FINDINGS["holed_closed"] is False, "an open mesh reported itself closed")
    expect(FINDINGS["holed_openness"] == "unexpectedly_open",
           f"an undeclared hole was not classified as unexpected: {FINDINGS['holed_openness']}")
    expect(FINDINGS["holed_non_manifold"] is False,
           "a boundary edge was reported as a non-manifold structural defect, which is the "
           "conflation that makes an intentionally open surface impossible to pass")
    expect("geometry.no_unintended_open_boundaries" in FINDINGS["holed_failing"],
           "an undeclared hole was not a defect")
    expect(FINDINGS["declared_loops"] == 1,
           "declaring the hole intentional hid the hole itself rather than the verdict")
    expect(FINDINGS["declared_unintended"] == 0, "a declared-open mesh still counted the hole")
    expect(FINDINGS["declared_openness"] == "intentionally_open",
           f"a declared-open mesh was not classified as such: {FINDINGS['declared_openness']}")
    expect(FINDINGS["declared_failing"] == [],
           f"a declared-open mesh was still a defect: {FINDINGS['declared_failing']}")

    expect(FINDINGS["flipped_edges"] > 0, "a flipped face was not found")
    expect("geometry.normals_consistent" in FINDINGS["flipped_failing"],
           "inconsistent winding did not fail its invariant")

    expect(FINDINGS["inverted_consistent"] == 0,
           "a uniformly reversed solid was reported as inconsistently wound")
    expect(FINDINGS["inverted_solids"] == 1, "an inside-out solid was not found")
    expect(FINDINGS["inverted_volume"] is not None and FINDINGS["inverted_volume"] < 0,
           f"the signed volume did not reveal the inversion: {FINDINGS['inverted_volume']}")
    expect(FINDINGS["inverted_failing"] == ["geometry.normals_outward"],
           f"an inversion broke the wrong invariants: {FINDINGS['inverted_failing']}")

    expect(FINDINGS["parts_components"] == 2, "a second island was not counted")
    expect(FINDINGS["parts_floating"] == 1, "a floating part was not reported as floating")
    expect("geometry.no_floating_components" in FINDINGS["parts_failing"],
           "a floating part did not fail its invariant")

    expect(FINDINGS["crossed_pairs"] > 0, "two boxes passing through each other were not found")
    expect("geometry.no_self_intersection" in FINDINGS["crossed_failing"],
           "a self-intersection did not fail its invariant")
    expect(FINDINGS["skipped_limits"] == ["geometry.no_self_intersection"],
           f"an unchecked property was not reported as unmeasured: {FINDINGS['skipped_limits']}")
    expect(FINDINGS["skipped_pairs"] == 0,
           "an unchecked search reported pairs it never looked for")

    expect(FINDINGS["degenerate_zero_edges"] > 0, "a collapsed edge was not found")
    expect(FINDINGS["degenerate_faces"] > 0, "a degenerate face was not found")
    expect("geometry.no_degenerate_faces" in FINDINGS["degenerate_failing"],
           "degenerate geometry did not fail its invariant")

    (artifact_dir("blender-truth-geometry") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_TRUTH_GEOMETRY", main, "blender-truth-geometry")
