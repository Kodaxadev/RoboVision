"""Reference shape truth, against references generated from known geometry.

The references here are produced by measuring a known mesh and writing its own
silhouette out as an image. That is deliberate: it makes the expected answer
exactly knowable, so the gate can assert "identical geometry scores ~1.0" rather
than asserting whatever the implementation happened to return. A hand-drawn
reference would only prove the metric runs.

What the gate exists to catch:

- a metric that cannot tell too-much from too-little, which are opposite
  corrections
- a comparison that reports the same error whichever view it was taken from,
  which would let a side-view defect be blamed on the front
- a comparison across two different cameras or projections treated as if the
  difference were geometry error
- a good score read as agreement about the whole asset, when a single reference
  constrains only one outline

The subject silhouette is raycast through the canonical camera rather than
rendered, so this whole gate runs headless. There is no shading, anti-aliasing or
render pipeline in the measurement, which is why it can be reproduced in CI
without a display.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from truth_support import cube, metrics  # noqa: E402

FINDINGS: dict[str, object] = {}
WORK = Path(bpy.app.tempdir) / "robovision-reference"

# Two orthogonal canonical views. Which ids these are is discovered rather than
# assumed, so the gate does not encode the sampler's internal ordering.
FRONT_AXIS = 1
SIDE_AXIS = 0


def write_mask(rv: Host, name: str, view: str, path: Path, size: int = 128) -> dict:
    """Render the current geometry's silhouette to a file, via the host's own view.

    Uses `truth.reference` machinery indirectly: the mask written here is the
    same geometric occupancy the measurement computes, so a later comparison of
    unchanged geometry must score perfectly. If it does not, the fault is in the
    measurement rather than in the fixture.
    """
    from mathutils.bvhtree import BVHTree

    from robovision_blender.truth import mask as masks
    from robovision_blender.truth import reference as reference_module
    from robovision_blender.truth import views as canonical

    objects = [bpy.data.objects[name]]
    points, indices, corners = reference_module._triangles(objects)
    frame = canonical.subject_frame(corners)
    cameras = canonical.camera_set(frame, level=1, projection="orthographic",
                                   width=size, height=size)
    camera = next(entry for entry in cameras if entry["view"] == view)
    tree = BVHTree.FromPolygons(points, indices, all_triangles=True)
    silhouette = masks.silhouette(tree, camera, frame)

    image = bpy.data.images.new(f"ref-{view}", width=size, height=size, alpha=True)
    try:
        pixels = [0.0] * (size * size * 4)
        for y in range(size):
            source = (size - 1 - y) * size  # write bottom-up as Blender stores it
            for x in range(size):
                on = 1.0 if silhouette[source + x] else 0.0
                base = (y * size + x) * 4
                pixels[base:base + 4] = [on, on, on, on]
        image.pixels = pixels
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)
    del rv
    # The frame the reference was captured in travels with it. Comparing a later
    # silhouette in a frame derived from the *changed* subject would let the
    # camera zoom out to fit whatever grew, cancelling the proportion error the
    # comparison exists to find.
    return {"center": frame["center"], "radius": frame["radius"]}


def axis_view(rv: Host, name: str, axis: int, sign: float) -> str:
    """The canonical view id looking down one axis, discovered from the contract."""
    contract = rv.result("truth.views", {"object": name, "level": 1})
    best = max(contract["cameras"], key=lambda camera: camera["direction"][axis] * sign)
    return best["view"]


def a_reference_of_itself_agrees_almost_exactly(rv: Host) -> None:
    clean_scene()
    cube(rv, "Box", size=2.0)
    WORK.mkdir(parents=True, exist_ok=True)
    front = axis_view(rv, "Box", FRONT_AXIS, -1.0)
    path = WORK / "box-front.png"
    frame = write_mask(rv, "Box", front, path)

    certificate = rv.result("truth.reference",
                            {"object": "Box", "reference": str(path), "view": front,
                             "frame": frame})
    values = metrics(certificate)
    FINDINGS["self_iou"] = round(values["reference.macro.silhouette_iou"], 4)
    FINDINGS["self_excess"] = round(values["reference.macro.excess_fraction"], 4)
    FINDINGS["self_deficit"] = round(values["reference.macro.deficit_fraction"], 4)
    FINDINGS["self_contour"] = round(values["reference.contour.mean_distance"], 6)
    FINDINGS["self_limits"] = sorted(certificate["limits"])
    FINDINGS["self_reference_hash"] = certificate["pins"]["reference"]["content_hash"]
    FINDINGS["self_view"] = certificate["pins"]["views"]["view"]
    FINDINGS["self_camera_recorded"] = "view_matrix" in certificate["pins"]["views"]["camera"]
    FINDINGS["self_no_quality_scalar"] = not any(
        name.endswith("reference_quality") for name in certificate["metrics"])
    FINDINGS["self_framing"] = certificate["pins"]["views"]["framing"]

    # Without a declared frame the comparison still runs, and says what it can no
    # longer promise. That distinction is the whole reason the frame exists.
    floating = rv.result("truth.reference",
                         {"object": "Box", "reference": str(path), "view": front})
    FINDINGS["floating_framing"] = floating["pins"]["views"]["framing"]
    FINDINGS["floating_limits"] = sorted(floating["limits"])


def widening_the_asset_shows_up_as_excess_not_deficit(rv: Host) -> None:
    """Too much material and too little are opposite corrections, so they differ."""
    clean_scene()
    cube(rv, "Box", size=2.0)
    front = axis_view(rv, "Box", FRONT_AXIS, -1.0)
    path = WORK / "box-front-narrow.png"
    frame = write_mask(rv, "Box", front, path)

    bpy.data.objects["Box"].scale = (1.6, 1.0, 1.0)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    certificate = rv.result("truth.reference",
                            {"object": "Box", "reference": str(path), "view": front,
                             "frame": frame})
    # The same widening measured in a frame that follows the subject: the camera
    # backs off to fit it and most of the proportion error cancels. Recorded so
    # the difference between the two framings is evidence rather than folklore.
    absorbed = rv.result("truth.reference",
                         {"object": "Box", "reference": str(path), "view": front})
    FINDINGS["wide_excess_subject_relative"] = round(
        metrics(absorbed)["reference.macro.excess_fraction"], 4)
    values = metrics(certificate)
    FINDINGS["wide_iou"] = round(values["reference.macro.silhouette_iou"], 4)
    FINDINGS["wide_excess"] = round(values["reference.macro.excess_fraction"], 4)
    FINDINGS["wide_deficit"] = round(values["reference.macro.deficit_fraction"], 4)
    FINDINGS["wide_aspect_error"] = round(values["reference.macro.aspect_error"], 4)
    FINDINGS["wide_contour"] = round(values["reference.contour.mean_distance"], 6)
    FINDINGS["wide_worst_sector"] = \
        certificate["metrics"]["reference.contour.worst_sector_net"]["detail"]["sector"]
    FINDINGS["wide_worst_sign"] = \
        certificate["metrics"]["reference.contour.worst_sector_net"]["detail"]["sign"]

    sectors = next(s for s in certificate["subjects"] if s["object"] == "__sectors__")
    horizontal = [s for s in sectors["sectors"] if s["sector"] in ("left", "right")]
    vertical = [s for s in sectors["sectors"] if s["sector"] in ("top", "bottom")]
    FINDINGS["wide_horizontal_excess"] = round(
        sum(s["excess_fraction"] for s in horizontal), 4)
    FINDINGS["wide_vertical_excess"] = round(
        sum(s["excess_fraction"] for s in vertical), 4)


def a_side_defect_is_invisible_from_the_front(rv: Host) -> None:
    """The case a single-view metric gets catastrophically wrong.

    The asset is stretched along the viewing axis of the front camera. From the
    front nothing has changed at all; from the side the profile is half again as
    deep. A measurement that reported error from the front view would be blaming
    the wrong geometry, and a model acting on it would edit the wrong thing.
    """
    clean_scene()
    cube(rv, "Box", size=2.0)
    front = axis_view(rv, "Box", FRONT_AXIS, -1.0)
    side = axis_view(rv, "Box", SIDE_AXIS, 1.0)
    front_path, side_path = WORK / "sd-front.png", WORK / "sd-side.png"
    front_frame = write_mask(rv, "Box", front, front_path)
    side_frame = write_mask(rv, "Box", side, side_path)

    # Deeper along the front camera's own view direction.
    bpy.data.objects["Box"].scale = (1.0, 1.5, 1.0)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    from_front = rv.result("truth.reference",
                           {"object": "Box", "reference": str(front_path), "view": front,
                            "frame": front_frame})
    from_side = rv.result("truth.reference",
                          {"object": "Box", "reference": str(side_path), "view": side,
                           "frame": side_frame})
    FINDINGS["depth_front_iou"] = round(
        metrics(from_front)["reference.macro.silhouette_iou"], 4)
    FINDINGS["depth_side_iou"] = round(
        metrics(from_side)["reference.macro.silhouette_iou"], 4)
    FINDINGS["depth_front_contour"] = round(
        metrics(from_front)["reference.contour.mean_distance"], 6)
    FINDINGS["depth_side_contour"] = round(
        metrics(from_side)["reference.contour.mean_distance"], 6)


def a_reference_from_a_different_camera_is_not_a_shape_error(rv: Host) -> None:
    """Two views compared as if they were one would call framing a defect."""
    clean_scene()
    cube(rv, "Box", size=2.0)
    bpy.data.objects["Box"].scale = (1.0, 1.0, 2.2)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    front = axis_view(rv, "Box", FRONT_AXIS, -1.0)
    top = axis_view(rv, "Box", 2, 1.0)
    front_path = WORK / "mismatch-front.png"
    frame = write_mask(rv, "Box", front, front_path)

    matched = rv.result("truth.reference",
                        {"object": "Box", "reference": str(front_path), "view": front,
                         "frame": frame})
    mismatched = rv.result("truth.reference",
                           {"object": "Box", "reference": str(front_path), "view": top,
                            "frame": frame})
    FINDINGS["matched_iou"] = round(
        metrics(matched)["reference.macro.silhouette_iou"], 4)
    FINDINGS["mismatched_iou"] = round(
        metrics(mismatched)["reference.macro.silhouette_iou"], 4)
    FINDINGS["matched_view"] = matched["pins"]["views"]["view"]
    FINDINGS["mismatched_view"] = mismatched["pins"]["views"]["view"]
    FINDINGS["views_distinguish_certificates"] = \
        matched["certificate"] != mismatched["certificate"]

    # An unknown view is refused rather than silently defaulted to something.
    refused = rv.call("truth.reference",
                      {"object": "Box", "reference": str(front_path), "view": "v999"},
                      ok=False, code="INVALID_PARAMS")
    FINDINGS["unknown_view_refused"] = refused["error"]["code"]
    missing = rv.call("truth.reference", {"object": "Box", "reference": str(front_path)},
                      ok=False, code="INVALID_PARAMS")
    FINDINGS["missing_view_refused"] = "view is required" in missing["error"]["message"]


def a_silhouette_can_be_produced_and_measured_against_itself(rv: Host) -> None:
    """The setup path a client outside the editor previously did not have.

    Producing a reference used to require reaching into Blender directly, so a
    comparison could be measured but never established from the public surface.
    This closes that: render the occupancy, then measure the same geometry
    against it in the same declared frame and expect exact agreement — which is
    only true if the two share one camera contract rather than two that agree by
    convention.
    """
    clean_scene()
    cube(rv, "Rendered", size=2.0)
    # Asymmetric in X and Y on purpose: a box with square plan has front and side
    # silhouettes of identical area, so occupied fraction could not tell the two
    # views apart and the check below would pass for the wrong reason.
    bpy.data.objects["Rendered"].scale = (1.0, 1.6, 1.7)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    front = axis_view(rv, "Rendered", FRONT_AXIS, -1.0)
    path = WORK / "produced.png"
    produced = rv.result("truth.silhouette",
                         {"object": "Rendered", "view": front, "path": str(path),
                          "width": 96, "height": 96})
    FINDINGS["produced_path_exists"] = Path(produced["artifact"]["path"]).is_file()
    FINDINGS["produced_fraction"] = round(produced["occupied_fraction"], 4)
    FINDINGS["produced_content"] = produced["content"]
    FINDINGS["produced_framing"] = produced["framing"]

    measured = rv.result("truth.reference",
                         {"object": "Rendered", "reference": produced["artifact"]["path"],
                          "view": front,
                          "frame": {"center": produced["frame"]["center"],
                                    "radius": produced["frame"]["radius"]}})
    FINDINGS["produced_iou"] = round(
        metrics(measured)["reference.macro.silhouette_iou"], 4)
    FINDINGS["produced_contour"] = round(
        metrics(measured)["reference.contour.mean_distance"], 6)

    # A different view of the same asset is a different silhouette, or the view
    # argument is doing nothing.
    side = axis_view(rv, "Rendered", SIDE_AXIS, 1.0)
    other = rv.result("truth.silhouette",
                      {"object": "Rendered", "view": side, "path": str(WORK / "other.png"),
                       "width": 96, "height": 96,
                       "frame": {"center": produced["frame"]["center"],
                                 "radius": produced["frame"]["radius"]}})
    FINDINGS["produced_views_differ"] = (
        round(other["occupied_fraction"], 6) != round(produced["occupied_fraction"], 6))

    # What the picture contains, which the picture cannot say.
    FINDINGS["produced_subjects"] = produced["subject_names"]
    over_covered = rv.result("truth.reference",
                             {"object": "Rendered",
                              "reference": produced["artifact"]["path"], "view": front,
                              "reference_subjects": produced["subjects"] + ["b3d:ghost"]})
    FINDINGS["subject_mismatch_limit"] = over_covered["limits"].get("reference.subject_set")
    matched = rv.result("truth.reference",
                        {"object": "Rendered",
                         "reference": produced["artifact"]["path"], "view": front,
                         "reference_subjects": produced["subjects"]})
    FINDINGS["subject_match_limit"] = matched["limits"].get("reference.subject_set")

    rv.call("truth.silhouette", {"object": "Rendered", "view": "v999"},
            ok=False, code="INVALID_PARAMS")
    rv.call("truth.silhouette", {"object": "Rendered"}, ok=False, code="INVALID_PARAMS")


def an_empty_reference_is_a_setup_error_not_a_score_of_zero(rv: Host) -> None:
    clean_scene()
    cube(rv, "Box", size=2.0)
    front = axis_view(rv, "Box", FRONT_AXIS, -1.0)
    path = WORK / "blank.png"
    image = bpy.data.images.new("blank", width=64, height=64, alpha=True)
    try:
        image.pixels = [0.0] * (64 * 64 * 4)
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)

    certificate = rv.result("truth.reference",
                            {"object": "Box", "reference": str(path), "view": front})
    FINDINGS["empty_metrics"] = sorted(certificate["metrics"])
    FINDINGS["empty_limits"] = sorted(certificate["limits"])


def main() -> None:
    rv = Host("truth-reference")
    a_reference_of_itself_agrees_almost_exactly(rv)
    widening_the_asset_shows_up_as_excess_not_deficit(rv)
    a_side_defect_is_invisible_from_the_front(rv)
    a_reference_from_a_different_camera_is_not_a_shape_error(rv)
    a_silhouette_can_be_produced_and_measured_against_itself(rv)
    an_empty_reference_is_a_setup_error_not_a_score_of_zero(rv)

    expect(FINDINGS["self_iou"] >= 0.999,
           f"identical geometry did not match its own silhouette: {FINDINGS['self_iou']}")
    expect(FINDINGS["self_contour"] <= 1e-6,
           f"identical geometry reported a contour distance: {FINDINGS['self_contour']}")
    expect(str(FINDINGS["self_reference_hash"]).startswith("rvref:"),
           "the reference was not content-hashed")
    expect(FINDINGS["self_camera_recorded"],
           "the comparison did not record the camera it was taken from")
    expect(FINDINGS["self_no_quality_scalar"],
           "a single collapsed reference-quality score was published")
    for limit in ("reference.constrains_hidden_geometry", "reference.camera_calibrated"):
        expect(limit in FINDINGS["self_limits"],
               f"the comparison did not declare {limit}: {FINDINGS['self_limits']}")

    expect(FINDINGS["wide_iou"] < 0.8,
           f"a 60% wider asset still matched its reference: {FINDINGS['wide_iou']}")
    expect(FINDINGS["wide_excess"] > 0.4,
           f"extra material was not reported as excess: {FINDINGS['wide_excess']}")
    expect(FINDINGS["wide_excess_subject_relative"] < FINDINGS["wide_excess"],
           "subject-relative framing did not absorb any of the proportion error, so "
           "the declared frame is doing nothing")
    expect(FINDINGS["floating_framing"] == "subject_relative"
           and FINDINGS["self_framing"] == "declared",
           "the certificate does not record which framing produced it")
    expect("reference.absolute_proportion" in FINDINGS["floating_limits"],
           f"a subject-relative comparison did not declare that scale is absorbed: "
           f"{FINDINGS['floating_limits']}")
    expect(FINDINGS["wide_deficit"] < 0.01,
           f"widening the asset was reported as missing material: "
           f"{FINDINGS['wide_deficit']}")
    expect(FINDINGS["wide_aspect_error"] > 0.1,
           "a changed proportion did not register as an aspect error")
    expect(FINDINGS["wide_worst_sign"] == "excess",
           f"the worst sector was not attributed to excess: {FINDINGS['wide_worst_sign']}")
    expect(FINDINGS["wide_worst_sector"] in ("left", "right"),
           f"horizontal widening was localised to the wrong sector: "
           f"{FINDINGS['wide_worst_sector']}")
    expect(FINDINGS["wide_horizontal_excess"] > FINDINGS["wide_vertical_excess"],
           f"widening reported as much vertical error as horizontal: "
           f"{FINDINGS['wide_vertical_excess']} vs {FINDINGS['wide_horizontal_excess']}")

    expect(FINDINGS["depth_front_iou"] >= 0.999,
           f"a depth change was blamed on the front view, which cannot see it: "
           f"{FINDINGS['depth_front_iou']}")
    expect(FINDINGS["depth_front_contour"] <= 1e-6,
           "the front view reported contour error for geometry it cannot see")
    expect(FINDINGS["depth_side_iou"] < 0.8,
           f"the side view did not see the depth change: {FINDINGS['depth_side_iou']}")
    expect(FINDINGS["depth_side_contour"] > FINDINGS["depth_front_contour"],
           "only the view that can see the defect should report it")

    expect(FINDINGS["matched_iou"] >= 0.999,
           "a reference compared against its own view did not agree")
    expect(FINDINGS["mismatched_iou"] < FINDINGS["matched_iou"],
           "a reference compared against a different camera scored the same, so the "
           "projection is not part of the comparison")
    expect(FINDINGS["matched_view"] != FINDINGS["mismatched_view"],
           "the two comparisons did not record different views")
    expect(FINDINGS["views_distinguish_certificates"],
           "two comparisons from different cameras share one certificate identity")
    expect(FINDINGS["unknown_view_refused"] == "INVALID_PARAMS",
           "an unknown view id was accepted")
    expect(FINDINGS["missing_view_refused"],
           "a silhouette comparison ran without being told which view it is against")

    expect(FINDINGS["produced_path_exists"], "the silhouette artifact was not written")
    expect(0.0 < FINDINGS["produced_fraction"] < 1.0,
           f"the produced silhouette occupies an implausible fraction of the frame: "
           f"{FINDINGS['produced_fraction']}")
    expect("occupancy" in FINDINGS["produced_content"],
           f"the artifact did not describe itself as geometric occupancy: "
           f"{FINDINGS['produced_content']}")
    expect(FINDINGS["produced_framing"] == "subject_relative",
           "the artifact did not record which framing produced it")
    expect(FINDINGS["produced_iou"] >= 0.999,
           f"geometry did not agree with a silhouette produced from itself, so the "
           f"producing and measuring cameras are not the same contract: "
           f"{FINDINGS['produced_iou']}")
    expect(FINDINGS["produced_contour"] <= 1e-6,
           "a silhouette measured against itself reported contour error")
    expect(FINDINGS["produced_subjects"] == ["Rendered"],
           f"the silhouette did not record what it depicted: "
           f"{FINDINGS['produced_subjects']}")
    expect("b3d:ghost" in (FINDINGS["subject_mismatch_limit"] or ""),
           f"a reference covering a subject the measurement does not was not named: "
           f"{FINDINGS['subject_mismatch_limit']}")
    expect(FINDINGS["subject_match_limit"] is None,
           f"a reference covering exactly the measured subjects was still flagged: "
           f"{FINDINGS['subject_match_limit']}")
    expect("reference.subject_set" in FINDINGS["self_limits"],
           f"a reference with no declared subjects did not say the set is unknown: "
           f"{FINDINGS['self_limits']}")

    expect(FINDINGS["produced_views_differ"],
           "two different canonical views produced the same silhouette")

    expect(FINDINGS["empty_metrics"] == [],
           f"an empty reference produced metrics: {FINDINGS['empty_metrics']}")
    expect("reference.silhouette_agreement" in FINDINGS["empty_limits"],
           f"an empty reference was scored rather than reported as a setup error: "
           f"{FINDINGS['empty_limits']}")

    (artifact_dir("blender-truth-reference") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_TRUTH_REFERENCE", main, "blender-truth-reference")
