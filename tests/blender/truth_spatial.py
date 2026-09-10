"""Coordinate and scale truth, and the comparison a correction is judged by.

Two halves that belong together because the second is worthless without the
first. A dimension is a number with a currency, so a measurement carries the
coordinate contract it was taken in; and a comparison across a changed contract,
a replaced world or a different set of subjects is refused rather than computed,
because a confident delta over two unrelated measurements is the most convincing
wrong answer this system could produce.

Also pinned here: measuring authors nothing, and measuring the same unchanged
state twice yields the same certificate identity. A loop that could not tell "no
change" from "a small change" has no way to detect an out-of-scope edit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from truth_support import cube, edit, failing, metrics  # noqa: E402

FINDINGS: dict[str, object] = {}


def size_and_pivot_are_measured_in_a_named_frame(rv: Host) -> None:
    clean_scene()
    cube(rv, "Sized", size=2.0)
    bpy.data.objects["Sized"].scale = (1.0, 1.0, 3.0)
    bpy.context.view_layer.update()

    certificate = rv.result("truth.spatial", {"object": "Sized"})
    values = metrics(certificate)
    FINDINGS["dim_x"] = round(values["spatial.dimension_x"], 3)
    FINDINGS["dim_z"] = round(values["spatial.dimension_z"], 3)
    FINDINGS["largest"] = round(values["spatial.largest_dimension"], 3)
    FINDINGS["frame"] = certificate["pins"]["frame"]["coordinate_contract"]
    FINDINGS["canonical_unit"] = certificate["pins"]["frame"]["canonical_unit"]
    FINDINGS["uniform"] = certificate["subjects"][0]["transform"]["uniform_scale"]
    FINDINGS["local_z"] = round(certificate["subjects"][0]["local_bounds"]["size"][2], 3)
    FINDINGS["limits"] = sorted(certificate["limits"])

    # A size limit is a brief, not a property of geometry. Without one there is
    # nothing to check and the certificate says so; with one, the same mesh
    # passes or fails according to what was declared.
    strict = rv.result("truth.spatial", {"object": "Sized", "max_dimension": 2.0})
    FINDINGS["strict_failing"] = failing(strict)
    loose = rv.result("truth.spatial", {"object": "Sized", "max_dimension": 20.0})
    FINDINGS["loose_failing"] = failing(loose)
    FINDINGS["loose_limits"] = sorted(loose["limits"])


def a_pivot_nowhere_near_its_geometry_is_a_defect(rv: Host) -> None:
    """The classic silently-wrong asset: it imports, renders and animates around nothing."""
    clean_scene()
    cube(rv, "Offset")
    # The mesh moves; the object does not. Nothing about the topology changes.
    edit("Offset", lambda bm: bmesh.ops.translate(bm, verts=bm.verts[:], vec=(10.0, 0.0, 0.0)))
    bpy.context.view_layer.update()

    certificate = rv.result("truth.spatial", {"object": "Offset"})
    pivot = certificate["subjects"][0]["pivot"]
    FINDINGS["pivot_inside"] = pivot["inside_bounds"]
    FINDINGS["pivot_offset"] = round(pivot["offset_from_bounds_center"], 3)
    FINDINGS["pivot_failing"] = failing(certificate)

    # And the geometry itself is still perfectly fine, which is the point: a
    # placement defect must not read as a geometry defect.
    geometry = rv.result("truth.geometry", {"object": "Offset"})
    FINDINGS["offset_geometry_failing"] = failing(geometry)


def a_certificate_is_deterministic_and_measuring_authors_nothing(rv: Host) -> None:
    clean_scene()
    cube(rv, "Stable")
    revision = rv.runtime.revision
    fingerprint = rv.fingerprint()

    first = rv.result("truth.measure", {"object": "Stable"})
    second = rv.result("truth.measure", {"object": "Stable"})
    FINDINGS["deterministic_geometry"] = (
        first["certificates"]["geometry"]["certificate"]
        == second["certificates"]["geometry"]["certificate"])
    FINDINGS["deterministic_spatial"] = (
        first["certificates"]["spatial"]["certificate"]
        == second["certificates"]["spatial"]["certificate"])
    FINDINGS["measure_kinds"] = first["kinds"]
    FINDINGS["measure_holds"] = first["invariants_hold"]
    FINDINGS["measure_revision_moved"] = rv.runtime.revision != revision
    FINDINGS["measure_fingerprint_moved"] = rv.fingerprint() != fingerprint
    FINDINGS["measure_object_count"] = len(bpy.data.objects)

    # A real change gives a different identity, so "did anything move?" is a
    # string comparison before it is a diff.
    edit("Stable", lambda bm: bmesh.ops.translate(bm, verts=[bm.verts[0]], vec=(0.5, 0, 0)))
    rv.runtime.mark_dirty()
    third = rv.result("truth.measure", {"object": "Stable"})
    FINDINGS["changed_certificate_differs"] = (
        third["certificates"]["spatial"]["certificate"]
        != first["certificates"]["spatial"]["certificate"])


def a_correction_is_judged_by_what_moved(rv: Host) -> None:
    """The Q0 → candidate → Q1 arithmetic, on a defect that is really repaired."""
    clean_scene()
    cube(rv, "Repairable")
    edit("Repairable", lambda bm: bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY"))

    before = rv.result("truth.geometry", {"object": "Repairable"})
    edit("Repairable", lambda bm: bmesh.ops.holes_fill(bm, edges=bm.edges[:]))
    rv.runtime.mark_dirty()
    after = rv.result("truth.geometry", {"object": "Repairable"})

    delta = rv.result("truth.compare", {"before": before, "after": after})
    FINDINGS["compare_identical"] = delta["identical"]
    FINDINGS["compare_improved"] = "geometry.unintended_boundary_loops" in delta["improved"]
    FINDINGS["compare_fixed"] = delta["invariants_fixed"]
    FINDINGS["compare_regressed"] = delta["regressed"]
    FINDINGS["compare_holds"] = delta["invariants_hold"]
    FINDINGS["compare_movement"] = \
        delta["metrics"]["geometry.unintended_boundary_loops"]["movement"]

    # An unchanged mesh compares as identical rather than as a tiny improvement.
    same = rv.result("truth.compare", {"before": after, "after": after})
    FINDINGS["same_identical"] = same["identical"]
    FINDINGS["same_improved"] = same["improved"]


def a_regression_is_reported_as_one(rv: Host) -> None:
    """Improving one metric while breaking an invariant must be visible as both."""
    clean_scene()
    cube(rv, "Regressing")
    before = rv.result("truth.geometry", {"object": "Regressing"})
    # A candidate that adds a floating part: nothing it did was an improvement.
    edit("Regressing", lambda bm: bmesh.ops.create_cube(
        bm, size=0.5, matrix=Matrix.Translation((5.0, 0.0, 0.0))))
    rv.runtime.mark_dirty()
    after = rv.result("truth.geometry", {"object": "Regressing"})

    delta = rv.result("truth.compare", {"before": before, "after": after})
    FINDINGS["regression_regressed"] = "geometry.floating_components" in delta["regressed"]
    FINDINGS["regression_broken"] = delta["invariants_broken"]
    FINDINGS["regression_holds"] = delta["invariants_hold"]
    FINDINGS["regression_neutral_changed"] = "geometry.components" in delta["changed"]


def two_unrelated_measurements_are_refused_rather_than_averaged(rv: Host) -> None:
    """The most convincing wrong answer this system could produce, refused."""
    clean_scene()
    cube(rv, "A")
    cube(rv, "B")
    first = rv.result("truth.geometry", {"object": "A"})
    second = rv.result("truth.geometry", {"object": "B"})

    refused = rv.call("truth.compare", {"before": first, "after": second},
                      ok=False, code="INCOMPARABLE_MEASUREMENTS")
    FINDINGS["subjects_reason"] = refused["error"]["data"]["reason"]

    stale = {**first, "pins": {**first["pins"], "world_incarnation": "rvworld:gone"}}
    refused_world = rv.call("truth.compare", {"before": stale, "after": first},
                            ok=False, code="INCOMPARABLE_MEASUREMENTS")
    FINDINGS["world_reason"] = refused_world["error"]["data"]["reason"]

    contract = {**first, "pins": {**first["pins"], "coordinate_contract": "rvcoord:0"}}
    refused_frame = rv.call("truth.compare", {"before": contract, "after": first},
                            ok=False, code="INCOMPARABLE_MEASUREMENTS")
    FINDINGS["contract_reason"] = refused_frame["error"]["data"]["reason"]

    kinds = rv.call("truth.compare",
                    {"before": first, "after": rv.result("truth.spatial", {"object": "A"})},
                    ok=False, code="INCOMPARABLE_MEASUREMENTS")
    FINDINGS["kind_reason"] = kinds["error"]["data"]["reason"]

    # The revision and the fingerprint are exactly what a correction changes, so
    # they must never be part of the refusal. Compared across a moved state, the
    # metrics are all unchanged — which is the answer, and it is only reachable
    # because the comparison was allowed to run at all.
    moved = {**first, "pins": {**first["pins"], "revision": first["pins"]["revision"] + 5,
                               "fingerprint": "deadbeef"}}
    allowed = rv.result("truth.compare", {"before": moved, "after": first})
    FINDINGS["moved_state_unchanged"] = (
        allowed["improved"] == [] and allowed["regressed"] == [] and allowed["changed"] == [])


def a_pattern_stays_comparable_when_a_member_is_restored(rv: Host) -> None:
    """The rule the first Artist Loop correction falsified.

    A pattern is identified by what was declared, not by whichever objects
    currently satisfy it. Requiring a stable membership made restoring a missing
    member unevaluable — the one correction the count invariant exists to
    demand — because adding it changed the subject set. Every other kind of
    measurement genuinely is about its subjects and still refuses.
    """
    clean_scene()
    for index in range(4):
        cube(rv, f"Bar_{index}", size=0.2)
        bpy.data.objects[f"Bar_{index}"].location = (index * 0.5, 0.0, 0.0)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    spec = {"kind": "linear", "count": 4, "axis": "x", "spacing": 0.5,
            "spacing_tolerance": 0.01}
    complete = rv.result("truth.pattern", {**spec, "members": [f"Bar_{i}" for i in range(4)]})
    missing = rv.result("truth.pattern", {**spec, "members": [f"Bar_{i}" for i in (0, 1, 3)]})

    delta = rv.result("truth.compare", {"before": missing, "after": complete})
    FINDINGS["pattern_restore_comparable"] = True
    FINDINGS["pattern_restore_fixed"] = delta["invariants_fixed"]
    FINDINGS["pattern_restore_improved"] = "pattern.spacing_max_error" in delta["improved"]

    # A different declaration is still a different measurement.
    other = rv.result("truth.pattern",
                      {**spec, "spacing": 0.25,
                       "members": [f"Bar_{i}" for i in range(4)]})
    refused = rv.call("truth.compare", {"before": other, "after": complete},
                      ok=False, code="INCOMPARABLE_MEASUREMENTS")
    FINDINGS["pattern_declaration_reason"] = refused["error"]["data"]["reason"]


def a_declared_asset_survives_a_part_being_added(rv: Host) -> None:
    """The rule the first Artist Loop run falsified a second time.

    Restoring a missing part changes the subject set of every asset-level
    measurement at once, so requiring a stable set made structural corrections —
    most corrections there are — impossible to evaluate. A caller that declares
    what the measurements are *of* may add or remove a part; a caller that names
    three objects and nothing else still cannot.
    """
    clean_scene()
    cube(rv, "PartA", size=1.0)
    cube(rv, "PartB", size=1.0)
    bpy.data.objects["PartB"].location = (2.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    declared = {"subject_set": "asset:rig"}
    before = rv.result("truth.geometry", {"objects": ["PartA", "PartB"], **declared})
    undeclared_before = rv.result("truth.geometry", {"objects": ["PartA", "PartB"]})

    cube(rv, "PartC", size=1.0)
    bpy.data.objects["PartC"].location = (4.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    after = rv.result("truth.geometry",
                      {"objects": ["PartA", "PartB", "PartC"], **declared})
    undeclared_after = rv.result("truth.geometry", {"objects": ["PartA", "PartB", "PartC"]})

    delta = rv.result("truth.compare", {"before": before, "after": after})
    FINDINGS["asset_grew_comparable"] = True
    FINDINGS["asset_grew_components"] = delta["metrics"]["geometry.components"]["movement"]

    refused = rv.call("truth.compare",
                      {"before": undeclared_before, "after": undeclared_after},
                      ok=False, code="INCOMPARABLE_MEASUREMENTS")
    FINDINGS["undeclared_reason"] = refused["error"]["data"]["reason"]

    other = rv.result("truth.geometry",
                      {"objects": ["PartA", "PartB", "PartC"], "subject_set": "asset:other"})
    mismatched = rv.call("truth.compare", {"before": before, "after": other},
                         ok=False, code="INCOMPARABLE_MEASUREMENTS")
    FINDINGS["different_set_reason"] = mismatched["error"]["data"]["reason"]


def main() -> None:
    rv = Host("truth-spatial")
    a_declared_asset_survives_a_part_being_added(rv)
    a_pattern_stays_comparable_when_a_member_is_restored(rv)
    size_and_pivot_are_measured_in_a_named_frame(rv)
    a_pivot_nowhere_near_its_geometry_is_a_defect(rv)
    a_certificate_is_deterministic_and_measuring_authors_nothing(rv)
    a_correction_is_judged_by_what_moved(rv)
    a_regression_is_reported_as_one(rv)
    two_unrelated_measurements_are_refused_rather_than_averaged(rv)

    expect(FINDINGS["dim_x"] == 2.0, f"the x dimension was wrong: {FINDINGS['dim_x']}")
    expect(FINDINGS["dim_z"] == 6.0,
           f"a scaled object was not measured in world space: {FINDINGS['dim_z']}")
    expect(FINDINGS["local_z"] == 2.0,
           f"the local extent was not reported beside the world one: {FINDINGS['local_z']}")
    expect(FINDINGS["largest"] == 6.0, "the largest dimension was wrong")
    expect(str(FINDINGS["frame"]).startswith("rvcoord:"),
           "dimensions were reported without the frame they were measured in")
    expect("metre" in str(FINDINGS["canonical_unit"]),
           f"the unit a dimension is in was not stated: {FINDINGS['canonical_unit']}")
    expect(FINDINGS["uniform"] is False, "a non-uniform scale was not reported")
    expect(FINDINGS["limits"] == ["spatial.within_declared_size"],
           f"a size gate was invented without a declared brief: {FINDINGS['limits']}")
    expect(FINDINGS["strict_failing"] == ["spatial.within_declared_size"],
           f"an oversized asset passed its declared limit: {FINDINGS['strict_failing']}")
    expect(FINDINGS["loose_failing"] == [],
           f"an asset within its limit failed: {FINDINGS['loose_failing']}")
    expect(FINDINGS["loose_limits"] == [],
           "a declared limit was still reported as unmeasured")

    expect(FINDINGS["pivot_inside"] is False, "a pivot ten metres away was reported as inside")
    expect(FINDINGS["pivot_offset"] == 10.0,
           f"the pivot offset was wrong: {FINDINGS['pivot_offset']}")
    expect("spatial.pivots_within_bounds" in FINDINGS["pivot_failing"],
           "a pivot outside its geometry did not fail its invariant")
    expect(FINDINGS["offset_geometry_failing"] == [],
           f"a placement defect was reported as a geometry defect: "
           f"{FINDINGS['offset_geometry_failing']}")

    expect(FINDINGS["deterministic_geometry"], "measuring unchanged geometry twice differed")
    expect(FINDINGS["deterministic_spatial"], "measuring unchanged placement twice differed")
    expect(FINDINGS["measure_kinds"] == ["geometry", "spatial"],
           f"the bundle did not measure both kinds: {FINDINGS['measure_kinds']}")
    expect(FINDINGS["measure_holds"] is True, "a clean cube did not hold its invariants")
    expect(FINDINGS["measure_revision_moved"] is False, "measuring advanced the revision")
    expect(FINDINGS["measure_fingerprint_moved"] is False, "measuring changed the scene")
    expect(FINDINGS["measure_object_count"] == 1, "measuring authored an object")
    expect(FINDINGS["changed_certificate_differs"],
           "a real change produced the same certificate identity")

    expect(FINDINGS["compare_identical"] is False, "a repaired mesh compared as unchanged")
    expect(FINDINGS["compare_improved"], "closing the hole did not read as an improvement")
    expect(FINDINGS["compare_movement"] == "improved",
           f"the repair moved the wrong way: {FINDINGS['compare_movement']}")
    expect("geometry.no_unintended_open_boundaries" in FINDINGS["compare_fixed"],
           f"the repaired invariant was not reported fixed: {FINDINGS['compare_fixed']}")
    expect(FINDINGS["compare_holds"] is True, "the repaired mesh still fails an invariant")
    expect(FINDINGS["same_identical"] is True,
           "comparing a certificate with itself was not identical")
    expect(FINDINGS["same_improved"] == [],
           "comparing a certificate with itself found an improvement")

    expect(FINDINGS["regression_regressed"], "a new floating part did not read as a regression")
    expect("geometry.no_floating_components" in FINDINGS["regression_broken"],
           f"the broken invariant was not reported: {FINDINGS['regression_broken']}")
    expect(FINDINGS["regression_holds"] is False,
           "a candidate that broke an invariant still reported its invariants holding")
    expect(FINDINGS["regression_neutral_changed"],
           "a neutral metric that moved was not reported as changed")

    expect(FINDINGS["subjects_reason"] == "different_subjects",
           f"two unrelated subjects were compared: {FINDINGS['subjects_reason']}")
    expect(FINDINGS["world_reason"] == "pin_changed:world_incarnation",
           f"a replaced world did not refuse comparison: {FINDINGS['world_reason']}")
    expect(FINDINGS["contract_reason"] == "pin_changed:coordinate_contract",
           f"a changed unit convention did not refuse comparison: {FINDINGS['contract_reason']}")
    expect(FINDINGS["kind_reason"] == "different_measurement_kind",
           f"two different measurements were compared: {FINDINGS['kind_reason']}")
    expect(FINDINGS["moved_state_unchanged"],
           "a moved revision or fingerprint interfered with the comparison, and those are "
           "exactly what a correction is supposed to change")

    expect(FINDINGS["asset_grew_comparable"],
           "adding a part to a declared asset made its measurements incomparable, "
           "which makes every structural correction unevaluable")
    expect(FINDINGS["asset_grew_components"] == "changed",
           f"the added part did not register: {FINDINGS['asset_grew_components']}")
    expect(FINDINGS["undeclared_reason"] == "different_subjects",
           f"an undeclared caller lost the strict default: "
           f"{FINDINGS['undeclared_reason']}")
    expect(FINDINGS["different_set_reason"] == "different_subject_set",
           f"two different declared assets compared as one: "
           f"{FINDINGS['different_set_reason']}")

    expect(FINDINGS["pattern_restore_comparable"],
           "restoring a missing pattern member was not comparable against its own "
           "declaration, which makes the count invariant impossible to satisfy")
    expect("pattern.count_matches" in FINDINGS["pattern_restore_fixed"],
           f"the restored count was not reported as fixed: "
           f"{FINDINGS['pattern_restore_fixed']}")
    expect(FINDINGS["pattern_restore_improved"],
           "closing the double gap did not read as a spacing improvement")
    expect(FINDINGS["pattern_declaration_reason"] == "different_pattern_declaration",
           f"two different declarations compared as one pattern: "
           f"{FINDINGS['pattern_declaration_reason']}")

    (artifact_dir("blender-truth-spatial") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_TRUTH_SPATIAL", main, "blender-truth-spatial")
