"""Deterministic view coverage, against an asset built to hide something.

The claim under test is not "we rendered some views". It is that RoboVision can
say which parts of a surface have actually been inspected and which have not, and
can then work out where to look next. The fixture is therefore an asset with a
genuinely concealed underside: a slab resting on a wider base, so the slab's
bottom face is real, front-facing from below, and occluded by the base from every
direction that could otherwise see it.

Three things the gate exists to catch:

- a coverage number that counts cameras rather than surface, which would call the
  hidden face observed as soon as enough views existed
- a per-object visibility test, which would call the slab's underside visible
  because nothing belonging to the *slab* is in the way
- a next-view recommendation that is a guess rather than a calculation

Coverage here is geometric — front-facing plus an unoccluded ray back to the
camera — so it runs in a background Blender with no graphics context at all.
That is deliberate: the definition of the observation must not depend on whether
a display happened to be available. Rendered silhouette evidence, which genuinely
needs one, uses the same canonical cameras and is proved separately.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from truth_support import cube, metrics  # noqa: E402

FINDINGS: dict[str, object] = {}


def stacked_asset(rv: Host) -> None:
    """A slab on a wider base. The slab's underside cannot be seen from anywhere."""
    clean_scene()
    cube(rv, "Base", size=4.0)
    bpy.data.objects["Base"].scale = (1.0, 1.0, 0.25)
    bpy.data.objects["Base"].location = (0.0, 0.0, 0.0)
    cube(rv, "Slab", size=2.0)
    bpy.data.objects["Slab"].location = (0.0, 0.0, 1.5)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()


def the_camera_set_is_derived_from_the_asset_and_not_the_viewport(rv: Host) -> None:
    stacked_asset(rv)
    contract = rv.result("truth.views", {"objects": ["Base", "Slab"], "level": 1})

    FINDINGS["view_count"] = contract["count"]
    FINDINGS["sampler"] = contract["sampler"]
    FINDINGS["frame_center"] = [round(v, 4) for v in contract["frame"]["center"]]
    first = contract["cameras"][0]
    FINDINGS["camera_keys"] = sorted(first)
    FINDINGS["camera_projection"] = first["projection"]
    FINDINGS["camera_target"] = [round(v, 4) for v in first["target"]]

    # Deterministic: the same subjects give the same cameras, whatever the file
    # was doing in between. Rotating a viewport must be irrelevant, and in
    # background Blender there is no viewport to rotate — which is the point.
    again = rv.result("truth.views", {"objects": ["Base", "Slab"], "level": 1})
    FINDINGS["views_deterministic"] = (
        [c["position"] for c in contract["cameras"]]
        == [c["position"] for c in again["cameras"]])
    FINDINGS["sampler_stable"] = again["sampler"] == contract["sampler"]

    # A different construction must not be mistakable for the same one.
    denser = rv.result("truth.views", {"objects": ["Base", "Slab"], "level": 2})
    FINDINGS["denser_count"] = denser["count"]
    FINDINGS["sampler_distinguishes_level"] = denser["sampler"] != contract["sampler"]
    perspective = rv.result("truth.views",
                            {"objects": ["Base", "Slab"], "level": 1,
                             "projection": "perspective"})
    FINDINGS["sampler_distinguishes_projection"] = \
        perspective["sampler"] != contract["sampler"]


def an_insufficient_camera_set_leaves_the_underside_unverified(rv: Host) -> None:
    """Two views from above cannot see underneath, and must not claim to."""
    stacked_asset(rv)
    contract = rv.result("truth.views", {"objects": ["Base", "Slab"], "level": 1})
    # The two most upward-looking candidates: a deliberately poor inspection.
    from_above = sorted(contract["cameras"], key=lambda c: -c["direction"][2])[:2]
    ids = [camera["view"] for camera in from_above]
    FINDINGS["above_directions"] = [[round(v, 3) for v in c["direction"]] for c in from_above]

    poor = rv.result("truth.coverage",
                     {"objects": ["Base", "Slab"], "level": 1, "views": ids,
                      "samples": 2048})
    values = metrics(poor)
    FINDINGS["poor_observed"] = round(values["coverage.observed_fraction"], 4)
    FINDINGS["poor_unobserved"] = round(values["coverage.unobserved_fraction"], 4)
    FINDINGS["poor_regions"] = values["coverage.unobserved_regions"]
    FINDINGS["poor_views_used"] = values["coverage.views_used"]
    FINDINGS["poor_limits"] = sorted(poor["limits"])

    regions = _named(poor, "__unobserved__")["regions"]
    FINDINGS["poor_worst_region_area"] = round(regions[0]["area"], 4)
    FINDINGS["poor_worst_below_slab"] = regions[0]["centroid"][2] < 1.5

    # The recommendation must be a calculation with a number attached.
    recommendation = _named(poor, "__next_view__")
    FINDINGS["next_view_id"] = recommendation["view"]
    FINDINGS["next_view_gain"] = round(recommendation["predicted_new_fraction"], 4)
    FINDINGS["next_view_downward"] = recommendation["direction"][2] < 0.0
    FINDINGS["next_view_has_camera"] = "view_matrix" in recommendation["camera"]

    # And taking it really does improve coverage by about what was predicted.
    improved = rv.result("truth.coverage",
                         {"objects": ["Base", "Slab"], "level": 1,
                          "views": ids + [recommendation["view"]], "samples": 2048})
    gained = (metrics(improved)["coverage.observed_fraction"]
              - values["coverage.observed_fraction"])
    FINDINGS["next_view_actual_gain"] = round(gained, 4)
    FINDINGS["next_view_prediction_matched"] = \
        abs(gained - recommendation["predicted_new_fraction"]) < 1e-4

    # No other unused candidate would have done better.
    best_alternative = 0.0
    for camera in contract["cameras"]:
        if camera["view"] in ids or camera["view"] == recommendation["view"]:
            continue
        trial = rv.result("truth.coverage",
                          {"objects": ["Base", "Slab"], "level": 1,
                           "views": ids + [camera["view"]], "samples": 2048})
        best_alternative = max(
            best_alternative,
            metrics(trial)["coverage.observed_fraction"] - values["coverage.observed_fraction"])
    FINDINGS["next_view_is_optimal"] = gained >= best_alternative - 1e-9
    FINDINGS["best_alternative_gain"] = round(best_alternative, 4)


def a_concealed_face_stays_unobserved_from_every_direction(rv: Host) -> None:
    """The whole sphere of cameras, and the underside is still not visible.

    This is the case a per-object visibility test gets wrong: nothing belonging
    to the slab occludes its own bottom face, so a test that did not raycast
    against the other subject would report full coverage of a face no camera can
    actually see.
    """
    stacked_asset(rv)
    full = rv.result("truth.coverage",
                     {"objects": ["Base", "Slab"], "level": 1, "samples": 4096})
    values = metrics(full)
    FINDINGS["full_observed"] = round(values["coverage.observed_fraction"], 4)
    FINDINGS["full_unobserved_area"] = round(values["coverage.unobserved_area"], 4)
    FINDINGS["full_next_view"] = _named(full, "__next_view__") is not None

    regions = _named(full, "__unobserved__")["regions"]
    FINDINGS["full_region_objects"] = sorted(
        {name for region in regions for name in region["objects"]})
    FINDINGS["full_regions_under_slab"] = all(
        region["centroid"][2] < 1.6 for region in regions)

    # A lone slab in mid-air has nothing to hide behind, so the same measurement
    # of the same mesh reaches full coverage. That is what proves the shortfall
    # above was occlusion rather than a sampler that cannot see downward.
    bpy.data.objects.remove(bpy.data.objects["Base"], do_unlink=True)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()
    alone = rv.result("truth.coverage", {"object": "Slab", "level": 1, "samples": 4096})
    FINDINGS["alone_observed"] = round(
        metrics(alone)["coverage.observed_fraction"], 4)
    FINDINGS["alone_next_view"] = _named(alone, "__next_view__")


def coverage_is_deterministic_and_declares_what_it_approximates(rv: Host) -> None:
    stacked_asset(rv)
    # The snapshot first, then the revision: `fingerprint()` is an authoritative
    # read and reconciles the pending scene edits, so a revision captured before
    # it is a baseline from a moment that has already passed. Measured — this
    # gate failed on its own bookkeeping before it failed on anything real.
    fingerprint = rv.fingerprint()
    revision = rv.runtime.revision

    first = rv.result("truth.coverage", {"objects": ["Base", "Slab"], "level": 1,
                                         "samples": 1024})
    second = rv.result("truth.coverage", {"objects": ["Base", "Slab"], "level": 1,
                                          "samples": 1024})
    FINDINGS["coverage_deterministic"] = first["certificate"] == second["certificate"]
    FINDINGS["coverage_authored_nothing"] = (
        rv.runtime.revision == revision and rv.fingerprint() == fingerprint)
    FINDINGS["coverage_sampler_pinned"] = first["pins"]["views"]["sampler"]
    FINDINGS["coverage_selected_pinned"] = len(first["pins"]["views"]["selected"])
    FINDINGS["coverage_unmeasured"] = sorted(first["limits"])

    # A declared minimum is a brief, like a size limit. Without one there is
    # nothing to gate on and the certificate says so.
    gated = rv.result("truth.coverage", {"objects": ["Base", "Slab"], "level": 1,
                                         "samples": 1024, "min_coverage": 0.99})
    FINDINGS["gated_failing"] = sorted(
        name for name, entry in gated["invariants"].items() if not entry["holds"])
    lenient = rv.result("truth.coverage", {"objects": ["Base", "Slab"], "level": 1,
                                           "samples": 1024, "min_coverage": 0.5})
    FINDINGS["lenient_failing"] = sorted(
        name for name, entry in lenient["invariants"].items() if not entry["holds"])


def _named(certificate: dict, name: str):
    for subject in certificate["subjects"]:
        if subject["object"] == name:
            return subject
    return None


def main() -> None:
    rv = Host("truth-coverage")
    the_camera_set_is_derived_from_the_asset_and_not_the_viewport(rv)
    an_insufficient_camera_set_leaves_the_underside_unverified(rv)
    a_concealed_face_stays_unobserved_from_every_direction(rv)
    coverage_is_deterministic_and_declares_what_it_approximates(rv)

    expect(FINDINGS["view_count"] == 42,
           f"the level-1 icosphere sampler did not produce 42 directions: "
           f"{FINDINGS['view_count']}")
    expect(FINDINGS["denser_count"] == 162,
           f"the level-2 sampler did not produce 162 directions: {FINDINGS['denser_count']}")
    expect(FINDINGS["views_deterministic"], "the same asset produced different cameras")
    expect(FINDINGS["sampler_stable"], "the sampler id changed between identical requests")
    expect(FINDINGS["sampler_distinguishes_level"],
           "two different camera constructions share one sampler id")
    expect(FINDINGS["sampler_distinguishes_projection"],
           "changing the projection did not change the sampler id")
    expect(FINDINGS["camera_projection"] == "orthographic",
           "the canonical projection was not the declared default")
    for field in ("view_matrix", "projection_matrix", "near_clip", "far_clip",
                  "width", "height", "position", "direction", "up", "target"):
        expect(field in FINDINGS["camera_keys"],
               f"a canonical camera does not record {field}")
    expect(FINDINGS["camera_target"] == FINDINGS["frame_center"],
           "cameras do not look at the asset-derived centre")

    expect(FINDINGS["poor_unobserved"] > 0.2,
           f"two views from above claimed to have inspected most of the asset: "
           f"{FINDINGS['poor_unobserved']}")
    expect(FINDINGS["poor_views_used"] == 2, "the measurement used views it was not given")
    expect(FINDINGS["poor_worst_below_slab"],
           "the largest unobserved region was not underneath the asset")
    # Grid cells are a partition; regions are what a model can act on. Reporting
    # one concealed underside as hundreds of "regions" is technically true and
    # useless, so adjacent cells are merged.
    expect(FINDINGS["poor_regions"] < 20,
           f"unobserved surface was reported as raw grid cells rather than as "
           f"regions: {FINDINGS['poor_regions']}")
    expect(FINDINGS["poor_worst_region_area"] > 1.0,
           f"the largest unobserved region was not merged into one patch: "
           f"{FINDINGS['poor_worst_region_area']}")
    expect("coverage.continuous_surface" in FINDINGS["poor_limits"],
           f"a sampled coverage fraction did not declare its approximation: "
           f"{FINDINGS['poor_limits']}")

    expect(FINDINGS["next_view_downward"],
           f"the recommended next view did not look at the unobserved underside: "
           f"{FINDINGS['next_view_id']}")
    expect(FINDINGS["next_view_gain"] > 0.0, "the recommendation predicted no gain")
    expect(FINDINGS["next_view_has_camera"],
           "the recommendation did not carry the camera it is recommending")
    expect(FINDINGS["next_view_prediction_matched"],
           f"the predicted gain did not match the measured one: predicted "
           f"{FINDINGS['next_view_gain']}, actual {FINDINGS['next_view_actual_gain']}")
    expect(FINDINGS["next_view_is_optimal"],
           f"another candidate would have revealed more surface: "
           f"{FINDINGS['best_alternative_gain']} > {FINDINGS['next_view_actual_gain']}")

    expect(FINDINGS["full_observed"] < 1.0,
           f"a concealed face was reported as observed from the full sphere: "
           f"{FINDINGS['full_observed']}")
    expect(FINDINGS["full_unobserved_area"] > 0.0, "the concealed area measured as zero")
    expect(FINDINGS["full_regions_under_slab"],
           "the unobserved surface was not where the occlusion is")
    expect(FINDINGS["alone_observed"] == 1.0,
           f"the same mesh with nothing occluding it was not fully observed: "
           f"{FINDINGS['alone_observed']}, so the shortfall above was the sampler "
           f"rather than the occlusion")
    expect(FINDINGS["alone_next_view"] is None,
           "a fully observed asset was still recommended another camera")

    expect(FINDINGS["coverage_deterministic"],
           "measuring coverage twice over unchanged geometry differed")
    expect(FINDINGS["coverage_authored_nothing"], "measuring coverage changed the scene")
    expect(str(FINDINGS["coverage_sampler_pinned"]).startswith("rvviews:"),
           "the certificate is not pinned to the camera construction it used")
    expect(FINDINGS["coverage_selected_pinned"] == 42,
           "the certificate does not record which views were actually used")
    expect(FINDINGS["gated_failing"] == ["coverage.meets_declared_minimum"],
           f"a declared coverage minimum was not enforced: {FINDINGS['gated_failing']}")
    expect(FINDINGS["lenient_failing"] == [],
           f"a met coverage minimum still failed: {FINDINGS['lenient_failing']}")
    expect("coverage.meets_declared_minimum" in FINDINGS["coverage_unmeasured"],
           "coverage without a declared minimum invented a gate")

    (artifact_dir("blender-truth-coverage") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_TRUTH_COVERAGE", main, "blender-truth-coverage")
