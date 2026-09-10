"""Pattern truth, against arrays broken one property at a time.

Repetition is where generated and hand-built hard-surface geometry drifts in ways
that survive a thumbnail: seven fins where eight were asked for, one fin a
millimetre out of line, one rotated a degree, one slightly larger than its
siblings. Each fixture below breaks exactly one of those and leaves the rest
correct, so a measurement that conflated any two of them fails here.

The distinction the gate is really defending is which failures are binary. A
missing member is simply wrong; spacing that is a fraction of a millimetre out is
a gradient a correction can move. If those were one number, a loop could not tell
"add the eighth fin" from "nudge the third one", and an array that is
imperceptibly uneven would be failed like a broken one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from truth_support import cube, metrics  # noqa: E402

FINDINGS: dict[str, object] = {}

SPACING = 0.065
FIN = 0.02


def fins(rv: Host, name: str, count: int, *, drop: int | None = None,
         displace: tuple[int, float] | None = None,
         rotate: tuple[int, float] | None = None,
         enlarge: tuple[int, float] | None = None) -> None:
    """A linear array of thin fins, optionally broken in exactly one way."""
    clean_scene()
    cube(rv, name, size=1.0)
    mesh = bpy.data.objects[name].data
    bm = bmesh.new()
    try:
        bm.clear()
        for index in range(count):
            if index == drop:
                continue
            offset = index * SPACING
            scale = 1.0
            if enlarge and enlarge[0] == index:
                scale = enlarge[1]
            if displace and displace[0] == index:
                offset += displace[1]
            matrix = Matrix.Translation((offset, 0.0, 0.0))
            if rotate and rotate[0] == index:
                matrix = matrix @ Matrix.Rotation(rotate[1], 4, "X")
            # Thin in x, tall in z: a fin, with a clear principal axis so a
            # rotated member is detectable at all.
            matrix = matrix @ Matrix.Diagonal((FIN, 0.4 * scale, 0.9 * scale, 1.0))
            bmesh.ops.create_cube(bm, size=1.0, matrix=matrix)
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()


def declaration(**overrides) -> dict:
    base = {
        "components_of": "Fins",
        "kind": "linear",
        "count": 8,
        "axis": "x",
        "spacing": SPACING,
        "spacing_tolerance": 0.001,
        "orientation_tolerance": 0.5,
        "dimension_variance": 0.01,
    }
    base.update(overrides)
    return base


def failing(certificate: dict) -> list[str]:
    return sorted(name for name, entry in certificate["invariants"].items()
                  if not entry["holds"])


def an_exact_array_satisfies_its_declaration(rv: Host) -> None:
    fins(rv, "Fins", 8)
    certificate = rv.result("truth.pattern", declaration())
    values = metrics(certificate)
    FINDINGS["exact_failing"] = failing(certificate)
    FINDINGS["exact_count"] = values["pattern.member_count"]
    FINDINGS["exact_spacing_mean"] = round(values["pattern.spacing_mean"], 6)
    FINDINGS["exact_spacing_max_error"] = round(values["pattern.spacing_max_error"], 9)
    FINDINGS["exact_angular"] = round(values["pattern.max_angular_deviation"], 6)
    FINDINGS["exact_dimension_cv"] = round(values["pattern.dimension_cv"], 6)
    FINDINGS["exact_limits"] = sorted(certificate["limits"])


def a_missing_member_fails_the_count_and_not_the_spacing(rv: Host) -> None:
    """A gap is not an uneven array. Those are different corrections.

    Removing the last fin leaves seven perfectly spaced ones, so the count is
    wrong and every gap is still exactly right — which is precisely the case that
    would be invisible to a measurement that averaged the two together.
    """
    fins(rv, "Fins", 8, drop=7)
    certificate = rv.result("truth.pattern", declaration())
    values = metrics(certificate)
    FINDINGS["missing_failing"] = failing(certificate)
    FINDINGS["missing_count"] = values["pattern.member_count"]
    FINDINGS["missing_spacing_max_error"] = round(values["pattern.spacing_max_error"], 9)
    FINDINGS["missing_count_evidence"] = \
        certificate["invariants"]["pattern.count_matches"]["evidence"]


def a_displaced_member_worsens_spacing_and_nothing_else(rv: Host) -> None:
    fins(rv, "Fins", 8, displace=(3, 0.004))
    certificate = rv.result("truth.pattern", declaration())
    values = metrics(certificate)
    FINDINGS["displaced_failing"] = failing(certificate)
    FINDINGS["displaced_max_error"] = round(values["pattern.spacing_max_error"], 6)
    FINDINGS["displaced_worst_gap"] = \
        certificate["metrics"]["pattern.spacing_max_error"]["detail"]["worst_gap_index"]
    FINDINGS["displaced_angular"] = round(values["pattern.max_angular_deviation"], 6)
    FINDINGS["displaced_dimension_cv"] = round(values["pattern.dimension_cv"], 6)

    # Within tolerance the same displacement is a metric that moved and not a
    # failure, which is what stops an imperceptibly uneven array being rejected.
    lenient = rv.result("truth.pattern", declaration(spacing_tolerance=0.01))
    FINDINGS["displaced_lenient_failing"] = failing(lenient)
    FINDINGS["displaced_lenient_error"] = round(
        metrics(lenient)["pattern.spacing_max_error"], 6)


def a_rotated_member_worsens_orientation_and_nothing_else(rv: Host) -> None:
    from math import radians

    fins(rv, "Fins", 8, rotate=(4, radians(6.0)))
    certificate = rv.result("truth.pattern", declaration())
    values = metrics(certificate)
    FINDINGS["rotated_failing"] = failing(certificate)
    FINDINGS["rotated_angular"] = round(values["pattern.max_angular_deviation"], 3)
    FINDINGS["rotated_worst_member"] = \
        certificate["metrics"]["pattern.max_angular_deviation"]["detail"]["worst_member"]
    FINDINGS["rotated_spacing_error"] = round(values["pattern.spacing_max_error"], 9)
    FINDINGS["rotated_count"] = values["pattern.member_count"]
    FINDINGS["rotated_dimension_cv"] = round(values["pattern.dimension_cv"], 6)


def an_inconsistent_member_worsens_dimensions_and_nothing_else(rv: Host) -> None:
    fins(rv, "Fins", 8, enlarge=(2, 1.25))
    certificate = rv.result("truth.pattern", declaration())
    values = metrics(certificate)
    FINDINGS["enlarged_failing"] = failing(certificate)
    FINDINGS["enlarged_dimension_cv"] = round(values["pattern.dimension_cv"], 4)
    FINDINGS["enlarged_spread"] = round(values["pattern.dimension_spread"], 4)
    FINDINGS["enlarged_spacing_error"] = round(values["pattern.spacing_max_error"], 9)


def a_radial_array_closes_its_own_circle(rv: Host) -> None:
    """A missing member in a ring looks evenly spaced unless the wrap gap counts."""
    clean_scene()
    cube(rv, "Ring", size=1.0)
    mesh = bpy.data.objects["Ring"].data

    def build(count: int, total: int) -> None:
        from math import cos, sin, pi

        bm = bmesh.new()
        try:
            bm.clear()
            for index in range(count):
                angle = 2.0 * pi * index / total
                matrix = (Matrix.Translation((cos(angle) * 0.5, sin(angle) * 0.5, 0.0))
                          @ Matrix.Rotation(angle, 4, "Z")
                          @ Matrix.Diagonal((0.12, 0.03, 0.03, 1.0)))
                bmesh.ops.create_cube(bm, size=1.0, matrix=matrix)
            bm.to_mesh(mesh)
            mesh.update()
        finally:
            bm.free()
        bpy.context.view_layer.update()
        rv.runtime.mark_dirty()

    spec = {"components_of": "Ring", "kind": "radial", "count": 6, "axis": "z",
            "center": [0.0, 0.0, 0.0], "spacing_tolerance": 0.5}
    build(6, 6)
    complete = rv.result("truth.pattern", spec)
    FINDINGS["radial_failing"] = failing(complete)
    FINDINGS["radial_spacing_mean"] = round(
        metrics(complete)["pattern.spacing_mean"], 3)
    FINDINGS["radial_max_error"] = round(
        metrics(complete)["pattern.spacing_max_error"], 3)

    build(5, 6)
    broken = rv.result("truth.pattern", spec)
    FINDINGS["radial_broken_failing"] = failing(broken)
    FINDINGS["radial_broken_max_error"] = round(
        metrics(broken)["pattern.spacing_max_error"], 3)


def an_asymmetric_pair_is_detected(rv: Host) -> None:
    clean_scene()
    cube(rv, "Left", size=0.4)
    cube(rv, "Right", size=0.4)
    bpy.data.objects["Left"].location = (-1.0, 0.0, 0.0)
    bpy.data.objects["Right"].location = (1.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    spec = {"members": ["Left", "Right"], "kind": "mirror", "count": 2, "axis": "x",
            "center": [0.0, 0.0, 0.0], "spacing_tolerance": 0.001}
    symmetric = rv.result("truth.pattern", spec)
    FINDINGS["mirror_failing"] = failing(symmetric)
    FINDINGS["mirror_error"] = round(metrics(symmetric)["pattern.spacing_max_error"], 6)

    bpy.data.objects["Right"].location = (1.03, 0.0, 0.0)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()
    skewed = rv.result("truth.pattern", spec)
    FINDINGS["mirror_broken_failing"] = failing(skewed)
    FINDINGS["mirror_broken_error"] = round(
        metrics(skewed)["pattern.spacing_max_error"], 4)


def undeclared_properties_are_measured_but_not_gated(rv: Host) -> None:
    """A tolerance nobody declared must not become one this system invented."""
    fins(rv, "Fins", 8, displace=(3, 0.004))
    bare = rv.result("truth.pattern",
                     {"components_of": "Fins", "kind": "linear", "count": 8, "axis": "x"})
    FINDINGS["bare_failing"] = failing(bare)
    FINDINGS["bare_limits"] = sorted(bare["limits"])
    FINDINGS["bare_has_spacing_metric"] = "pattern.spacing_max_error" in bare["metrics"]


def main() -> None:
    rv = Host("truth-pattern")
    an_exact_array_satisfies_its_declaration(rv)
    a_missing_member_fails_the_count_and_not_the_spacing(rv)
    a_displaced_member_worsens_spacing_and_nothing_else(rv)
    a_rotated_member_worsens_orientation_and_nothing_else(rv)
    an_inconsistent_member_worsens_dimensions_and_nothing_else(rv)
    a_radial_array_closes_its_own_circle(rv)
    an_asymmetric_pair_is_detected(rv)
    undeclared_properties_are_measured_but_not_gated(rv)

    expect(FINDINGS["exact_failing"] == [],
           f"an exact array failed its own declaration: {FINDINGS['exact_failing']}")
    expect(FINDINGS["exact_count"] == 8, "the eight fins were not found as eight members")
    expect(abs(FINDINGS["exact_spacing_mean"] - SPACING) < 1e-6,
           f"the measured pitch was not the built one: {FINDINGS['exact_spacing_mean']}")
    expect(FINDINGS["exact_spacing_max_error"] < 1e-6, "an exact array reported error")
    expect(FINDINGS["exact_angular"] < 1e-3, "identical members disagreed about orientation")
    expect("pattern.member_roll" in FINDINGS["exact_limits"],
           f"the orientation measurement did not declare what it cannot see: "
           f"{FINDINGS['exact_limits']}")

    expect(FINDINGS["missing_failing"] == ["pattern.count_matches"],
           f"a missing member broke more than the count: {FINDINGS['missing_failing']}")
    expect(FINDINGS["missing_count"] == 7, "the remaining members were miscounted")
    expect(FINDINGS["missing_spacing_max_error"] < 1e-6,
           f"seven correctly spaced fins were also reported as unevenly spaced: "
           f"{FINDINGS['missing_spacing_max_error']}")
    expect(FINDINGS["missing_count_evidence"]["expected"] == 8
           and FINDINGS["missing_count_evidence"]["found"] == 7,
           "the count failure did not carry what was expected and what was found")

    expect(FINDINGS["displaced_failing"] != [], "a displaced member passed within tolerance")
    expect("pattern.spacing_within_tolerance" in FINDINGS["displaced_failing"],
           f"displacement did not fail the spacing gate: {FINDINGS['displaced_failing']}")
    expect("pattern.count_matches" not in FINDINGS["displaced_failing"],
           "displacing a member was reported as a missing one")
    expect(FINDINGS["displaced_angular"] < 1e-3,
           "moving a member was reported as rotating it")
    expect(FINDINGS["displaced_dimension_cv"] < 1e-6,
           "moving a member was reported as resizing it")
    expect(FINDINGS["displaced_lenient_failing"] == [],
           f"a displacement inside its declared tolerance was still a failure: "
           f"{FINDINGS['displaced_lenient_failing']}")
    expect(FINDINGS["displaced_lenient_error"] == FINDINGS["displaced_max_error"],
           "the measured error changed when only the tolerance did")

    expect("pattern.orientation_within_tolerance" in FINDINGS["rotated_failing"],
           f"a rotated member did not fail orientation: {FINDINGS['rotated_failing']}")
    expect(FINDINGS["rotated_angular"] > 5.0,
           f"a six degree rotation measured as {FINDINGS['rotated_angular']}")
    expect(FINDINGS["rotated_worst_member"] is not None,
           "the rotated member was not identified")
    expect(FINDINGS["rotated_spacing_error"] < 1e-6,
           "rotating a member was reported as moving it")
    expect(FINDINGS["rotated_count"] == 8, "rotating a member changed the count")
    # The one this gate originally let through. Member extent was read from a
    # world-axis-aligned box, which grows when a member is merely turned, so a
    # six degree rotation also failed dimensional consistency: "dimension"
    # silently meant "dimension and orientation", and rotating and resizing are
    # different corrections.
    expect(FINDINGS["rotated_failing"] == ["pattern.orientation_within_tolerance"],
           f"rotating a member broke something other than orientation: "
           f"{FINDINGS['rotated_failing']}")
    expect(FINDINGS["rotated_dimension_cv"] < 1e-4,
           f"rotating a member was reported as resizing it: "
           f"{FINDINGS['rotated_dimension_cv']}")

    expect("pattern.dimensions_consistent" in FINDINGS["enlarged_failing"],
           f"an oversized member did not fail dimensional consistency: "
           f"{FINDINGS['enlarged_failing']}")
    expect(FINDINGS["enlarged_dimension_cv"] > 0.01, "the size difference measured as zero")
    expect(FINDINGS["enlarged_spacing_error"] < 1e-6,
           "resizing a member was reported as moving it")

    expect(FINDINGS["radial_failing"] == [],
           f"an exact ring failed: {FINDINGS['radial_failing']}")
    expect(abs(FINDINGS["radial_spacing_mean"] - 60.0) < 0.01,
           f"a six-member ring did not measure 60 degree pitch: "
           f"{FINDINGS['radial_spacing_mean']}")
    expect("pattern.count_matches" in FINDINGS["radial_broken_failing"],
           "a ring missing a member passed its count")
    expect(FINDINGS["radial_broken_max_error"] > 30.0,
           f"the wrap-around gap was not counted, so a ring with a hole looked even: "
           f"{FINDINGS['radial_broken_max_error']}")

    expect(FINDINGS["mirror_failing"] == [],
           f"a symmetric pair failed: {FINDINGS['mirror_failing']}")
    expect(FINDINGS["mirror_error"] < 1e-6, "a symmetric pair reported a residual")
    expect(FINDINGS["mirror_broken_failing"] != [],
           "a 3cm asymmetry passed as symmetric")
    expect(FINDINGS["mirror_broken_error"] > 0.01,
           f"the asymmetry measured as {FINDINGS['mirror_broken_error']}")

    expect(FINDINGS["bare_failing"] == [],
           f"a declaration with no tolerances invented gates: {FINDINGS['bare_failing']}")
    expect(FINDINGS["bare_has_spacing_metric"],
           "an undeclared tolerance also suppressed the measurement")
    for name in ("pattern.spacing_within_tolerance", "pattern.orientation_within_tolerance",
                 "pattern.dimensions_consistent"):
        expect(name in FINDINGS["bare_limits"],
               f"{name} was neither gated nor declared unmeasured: {FINDINGS['bare_limits']}")

    (artifact_dir("blender-truth-pattern") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_TRUTH_PATTERN", main, "blender-truth-pattern")
