"""How the asset's observed profile differs from a reference, and what that proves.

The honest scope of this measurement is narrow and stating it is half the value.
A concept image constrains the outline it actually shows, from the viewpoint it
was drawn at. It says nothing about the back, nothing about the inside, and
nothing about detail that does not reach the silhouette. So the certificate
records what the comparison *constrains* and lists the rest as unconstrained,
rather than letting a good IoU read as agreement about the whole asset.

The second thing worth stating: two silhouettes are only comparable in one
camera and one projection. Comparing a perspective photograph against an
orthographic verification view and calling the difference geometry error would
attribute the projection's own foreshortening to the modeller. The camera the
subject mask was rendered from is recorded, the caller's claim about which view
the reference corresponds to is recorded *as a claim*, and the difference between
a declared alignment and a calibrated one is in `limits` — because nothing here
solves for the reference's true camera.

No single `reference_quality` number. Agreement is hierarchical — macro
proportion, then contour placement, then local correspondence — and collapsing it
early would have to be undone by the work that measures the lower levels
properly.
"""
from __future__ import annotations

from typing import Any

from mathutils.bvhtree import BVHTree

from ..registry import HostError
from . import mask as masks
from . import views as canonical_views
from .certificate import HIGHER_BETTER, LOWER_BETTER, NEUTRAL, Measurement

REFERENCE_SCHEMA = 1


def _load(path: str) -> tuple[list[float], int, int, str, bool]:
    """Read the reference, hash exactly what was read, and say if it has alpha."""
    import bpy

    from pathlib import Path

    source = Path(bpy.path.abspath(path))
    if not source.is_file():
        raise HostError("NOT_FOUND", f"reference image not found: {path}",
                        data={"path": str(source)})
    digest = masks.content_hash(source.read_bytes())

    image = bpy.data.images.load(str(source), check_existing=False)
    try:
        width, height = image.size
        if width == 0 or height == 0:
            raise HostError("INVALID_PARAMS", "reference image has no pixels",
                            data={"path": str(source)})
        pixels = list(image.pixels)
        has_alpha = image.depth in (32, 64, 128)
        return pixels, width, height, digest, has_alpha
    finally:
        bpy.data.images.remove(image)


def _triangles(objects: list):
    import bmesh
    from mathutils import Vector

    points: list[Vector] = []
    indices: list[tuple[int, int, int]] = []
    corners: list[Vector] = []
    for obj in objects:
        matrix = obj.matrix_world
        bm = bmesh.new()
        try:
            bm.from_mesh(obj.data)
            bmesh.ops.triangulate(bm, faces=bm.faces[:])
            for face in bm.faces:
                if len(face.verts) != 3:
                    continue
                base = len(points)
                points.extend(matrix @ vert.co for vert in face.verts)
                indices.append((base, base + 1, base + 2))
        finally:
            bm.free()
        corners.extend(matrix @ Vector(corner) for corner in obj.bound_box)
    return points, indices, corners


def measure(objects: list, *, path: str, view: str, level: int, projection: str,
            threshold: float, use_alpha: bool | None, alignment: str,
            frame_override: dict[str, Any] | None, runtime) -> Measurement:
    """Compare one canonical view's silhouette against one reference image.

    The framing is the subtle part, and getting it wrong makes the whole metric
    lie. Canonical cameras normally frame on the subject's own bounds, which is
    right for coverage — you want the whole asset in shot — and wrong here: a
    subject that grew is framed from further away, so its silhouette occupies
    much the same part of the frame and the proportion error largely cancels.
    Measured: a box widened by 60% reported 24% excess rather than 60%.

    So a reference comparison measures in a *fixed* frame, supplied by the caller
    and normally captured alongside the reference itself. Without one the
    comparison still runs — a caller may only care about outline shape — but the
    certificate records that scale is absorbed by the framing rather than
    letting a proportion number be quietly wrong.
    """
    from ..identity import mesh_revision, object_id
    from .certificate import pins_for

    pixels, width, height, digest, image_has_alpha = _load(path)
    points, indices, corners = _triangles(objects)
    if not indices:
        raise HostError("INVALID_PARAMS", "the subjects have no geometry to compare")

    frame = frame_override or canonical_views.subject_frame(corners)
    cameras = canonical_views.camera_set(frame, level=level, projection=projection,
                                         width=width, height=height)
    by_id = {camera["view"]: camera for camera in cameras}
    if view not in by_id:
        raise HostError("INVALID_PARAMS", f"unknown view id for this sampler: {view}",
                        data={"sampler": canonical_views.sampler_id(
                            level, projection, canonical_views.FRAMING_MARGIN),
                            "count": len(cameras)})
    camera = by_id[view]

    tree = BVHTree.FromPolygons(points, indices, all_triangles=True)
    subject_mask = masks.silhouette(tree, camera, frame)
    resolved_alpha = image_has_alpha if use_alpha is None else use_alpha
    reference_mask = masks.from_image(pixels, width, height,
                                      threshold=threshold, use_alpha=resolved_alpha)

    measurement = Measurement("reference", {})
    if masks.area(reference_mask) == 0:
        # A reference that thresholded to nothing is a setup error, not a subject
        # that fails to match it. Reporting 0.0 agreement would be a confident
        # number about the caller's threshold.
        measurement.unmeasured("reference.silhouette_agreement",
                               "the reference image thresholded to an empty mask; "
                               "check threshold and whether alpha should be used")
        measurement.pins = pins_for(runtime, [])
        _provenance(measurement, camera, frame, digest, path, view, level, projection,
                    threshold, resolved_alpha, alignment, width, height,
                    frame_override is not None)
        return measurement

    _record(measurement, subject_mask, reference_mask, width, height)

    measurement.subjects = [{
        "object": object_id(obj),
        "name": obj.name,
        "mesh_revision": mesh_revision(obj),
    } for obj in objects]
    measurement.subjects.append({
        "object": "__sectors__",
        "mesh_revision": None,
        "sectors": masks.sectors(subject_mask, reference_mask, width, height),
    })
    measurement.pins = pins_for(runtime, measurement.subjects)
    _provenance(measurement, camera, frame, digest, path, view, level, projection,
                threshold, resolved_alpha, alignment, width, height,
                frame_override is not None)
    return measurement


def _provenance(measurement: Measurement, camera, frame, digest, path, view, level,
                projection, threshold, use_alpha, alignment, width, height,
                framing_declared: bool) -> None:
    """Everything needed to say which comparison this number came from.

    The point of recording all of it is that a contour discrepancy has to be
    attributable: this subject, from this exact camera, against this exact
    reference, at this revision. Evidence that floats free of its viewpoint is
    not evidence about geometry.
    """
    measurement.pins["reference"] = {
        "schema": REFERENCE_SCHEMA,
        "content_hash": digest,
        "source": path,
        "width": width,
        "height": height,
        "mask_method": f"threshold:{'alpha' if use_alpha else 'luminance'}",
        "mask_schema": masks.MASK_SCHEMA,
        "threshold": threshold,
        "alignment": alignment,
    }
    measurement.pins["views"] = {
        "sampler": canonical_views.sampler_id(level, projection,
                                              canonical_views.FRAMING_MARGIN),
        "view": view,
        "projection": projection,
        "frame": frame,
        "framing": "declared" if framing_declared else "subject_relative",
        "camera": camera,
    }
    measurement.notes.append(
        "the subject silhouette is geometric occupancy, raycast through this exact "
        "camera rather than rendered, so it carries no shading, anti-aliasing or "
        "render-pipeline dependence")
    # The scope claim, stated rather than left to be inferred from a good score.
    measurement.unmeasured(
        "reference.constrains_hidden_geometry",
        f"a single reference constrains only the silhouette from view {view}; "
        f"geometry that does not reach this outline is unconstrained by it")
    if not framing_declared:
        # The one that would silently corrupt a proportion metric. Stated rather
        # than left for a caller to discover from a number that looks plausible.
        measurement.unmeasured(
            "reference.absolute_proportion",
            "the camera was framed on the subject's own bounds, so a change in "
            "overall size is partly absorbed by the framing and proportion error "
            "is under-reported; pass the frame captured with the reference to "
            "measure in a fixed space")
    if alignment == "declared":
        measurement.unmeasured(
            "reference.camera_calibrated",
            "the reference is assumed to correspond to this canonical view; no "
            "camera solve was performed, so a mismatch in the reference's own "
            "viewpoint would appear as shape error")


def _record(measurement: Measurement, subject: list[bool], reference: list[bool],
            width: int, height: int) -> None:
    """Macro agreement and contour agreement, kept as separate levels.

    Namespaced so the levels the research argues for — macro proportion,
    secondary distribution, local correspondence, contour — can be filled in
    later without renaming anything or unpicking a scalar.
    """
    overlap, excess, deficit = masks.iou(subject, reference)
    subject_area = masks.area(subject)
    reference_area = masks.area(reference)
    diagonal = (width * width + height * height) ** 0.5

    measurement.metric("reference.macro.silhouette_iou", round(overlap, 6),
                       direction=HIGHER_BETTER, unit="fraction")
    measurement.metric("reference.macro.excess_fraction",
                       round(excess / reference_area, 6),
                       direction=LOWER_BETTER, unit="fraction_of_reference")
    measurement.metric("reference.macro.deficit_fraction",
                       round(deficit / reference_area, 6),
                       direction=LOWER_BETTER, unit="fraction_of_reference")
    measurement.metric("reference.macro.area_ratio",
                       round(subject_area / reference_area, 6),
                       direction=NEUTRAL, unit="ratio")

    subject_bounds = masks.bounds(subject, width, height)
    reference_bounds = masks.bounds(reference, width, height)
    if subject_bounds and reference_bounds:
        measurement.metric(
            "reference.macro.aspect_error",
            round(abs(subject_bounds["aspect"] - reference_bounds["aspect"]), 6),
            direction=LOWER_BETTER, unit="ratio",
            subject_aspect=subject_bounds["aspect"],
            reference_aspect=reference_bounds["aspect"])
        for axis in ("width", "height"):
            measurement.metric(
                f"reference.macro.{axis}_ratio",
                round(subject_bounds[axis] / reference_bounds[axis], 6),
                direction=NEUTRAL, unit="ratio")

    subject_contour = masks.contour(subject, width, height)
    reference_contour = masks.contour(reference, width, height)
    distances = masks.chamfer(subject_contour, reference_contour, width, height)
    # Normalised by the image diagonal so the number means the same thing at any
    # mask resolution; the raw pixel value is kept beside it because a model
    # reasoning about a specific image sometimes wants it.
    measurement.metric("reference.contour.mean_distance",
                       round(distances["mean"] / diagonal, 6),
                       direction=LOWER_BETTER, unit="fraction_of_diagonal",
                       pixels=round(distances["mean"], 4))
    measurement.metric("reference.contour.max_distance",
                       round(distances["max"] / diagonal, 6),
                       direction=LOWER_BETTER, unit="fraction_of_diagonal",
                       pixels=round(distances["max"], 4))
    measurement.metric("reference.contour.subject_to_reference",
                       round(distances["subject_to_reference"] / diagonal, 6),
                       direction=LOWER_BETTER, unit="fraction_of_diagonal")
    measurement.metric("reference.contour.reference_to_subject",
                       round(distances["reference_to_subject"] / diagonal, 6),
                       direction=LOWER_BETTER, unit="fraction_of_diagonal")

    worst = max(masks.sectors(subject, reference, width, height),
                key=lambda entry: abs(entry["net_fraction"]))
    measurement.metric("reference.contour.worst_sector_net",
                       round(abs(worst["net_fraction"]), 6),
                       direction=LOWER_BETTER, unit="fraction_of_sector",
                       sector=worst["sector"],
                       sign="excess" if worst["net_fraction"] > 0 else "deficit")
