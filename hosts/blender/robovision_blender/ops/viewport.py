from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
import uuid

import bpy
from mathutils import Vector

from ..context import view3d_override
from ..identity import resolve_object
from ..registry import HostError


PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")


def _matrix(value):
    return [[float(component) for component in row] for row in value]


def _png_size(path) -> dict | None:
    """Read width/height straight from the PNG IHDR chunk.

    The artifact's real pixel dimensions are provenance: `render.opengl` writes
    at the scene render resolution, which is not the interactive region size.
    """
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != PNG_MAGIC or header[12:16] != b"IHDR":
        return None
    return {
        "width": int.from_bytes(header[16:20], "big"),
        "height": int.from_bytes(header[20:24], "big"),
    }


def _metadata(area, region, space, runtime):
    rv3d = space.region_3d
    render = bpy.context.scene.render
    scale = max(1, int(render.resolution_percentage)) / 100.0
    return {
        "scene_revision": runtime.revision,
        "region": {"width": int(region.width), "height": int(region.height)},
        "viewport": {"width": int(region.width), "height": int(region.height)},
        "render_output": {
            "width": int(render.resolution_x * scale),
            "height": int(render.resolution_y * scale),
            "resolution_percentage": int(render.resolution_percentage),
        },
        "projection": rv3d.view_perspective,
        "view_location": list(rv3d.view_location),
        "view_rotation": list(rv3d.view_rotation),
        "view_distance": float(rv3d.view_distance),
        "view_matrix": _matrix(rv3d.view_matrix),
        "perspective_matrix": _matrix(rv3d.perspective_matrix),
        "lens_mm": float(space.lens),
        "clip_start": float(space.clip_start),
        "clip_end": float(space.clip_end),
        "shading": space.shading.type,
        "overlays": bool(space.overlay.show_overlays),
    }


def inspect(_params, runtime):
    with view3d_override() as (area, region, space):
        return _metadata(area, region, space, runtime)


def focus(params, runtime):
    obj = resolve_object(params.get("object"))
    padding = max(1.0, float(params.get("padding", 1.5)))
    with view3d_override() as (area, region, space):
        rv3d = space.region_3d
        if obj.type == "EMPTY" or not getattr(obj, "bound_box", None):
            center = obj.matrix_world.translation.copy()
            radius = max(0.25, float(obj.empty_display_size))
        else:
            corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            center = sum(corners, Vector()) / len(corners)
            radius = max((corner - center).length for corner in corners)
            radius = max(radius, 0.01)
        rv3d.view_location = center
        rv3d.view_distance = radius * padding * 2.0
        return _metadata(area, region, space, runtime)


def axis(params, runtime):
    axis_name = str(params.get("axis", "FRONT")).upper()
    if axis_name not in {"FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"}:
        raise HostError("INVALID_PARAMS", "axis must be FRONT, BACK, LEFT, RIGHT, TOP, or BOTTOM")
    with view3d_override() as (area, region, space):
        if not bpy.ops.view3d.view_axis.poll():
            raise HostError("INVALID_CONTEXT", "view-axis operation is unavailable")
        # Smooth view animates the transition, so metadata captured immediately
        # after the operator could describe a view still in flight. Evidence has
        # to match the frame it claims to describe.
        preferences = bpy.context.preferences.view
        smooth_view = preferences.smooth_view
        try:
            preferences.smooth_view = 0
            result = bpy.ops.view3d.view_axis(type=axis_name, align_active=False)
        finally:
            preferences.smooth_view = smooth_view
        if "FINISHED" not in result:
            raise HostError("HOST_EXCEPTION", "Blender did not change the viewport axis")
        return _metadata(area, region, space, runtime)


def capture(params, runtime):
    output = params.get("path")
    if output is None:
        directory = Path(tempfile.gettempdir()) / "robovision"
        directory.mkdir(parents=True, exist_ok=True)
        output_path = directory / f"viewport-{time.time_ns()}-{uuid.uuid4().hex[:8]}.png"
    else:
        output_path = Path(str(output)).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() != ".png":
            raise HostError("INVALID_PARAMS", "viewport capture path must end in .png")

    requested_shading = params.get("shading")
    if requested_shading is not None:
        requested_shading = str(requested_shading).upper()
        if requested_shading not in {"WIREFRAME", "SOLID", "MATERIAL", "RENDERED"}:
            raise HostError("INVALID_PARAMS", "shading must be WIREFRAME, SOLID, MATERIAL, or RENDERED")

    scene = bpy.context.scene
    old_path = scene.render.filepath
    old_format = scene.render.image_settings.file_format
    with view3d_override() as (area, region, space):
        old_shading = space.shading.type
        old_overlays = space.overlay.show_overlays
        try:
            if requested_shading is not None:
                space.shading.type = requested_shading
            if "overlays" in params:
                space.overlay.show_overlays = bool(params["overlays"])
            scene.render.filepath = str(output_path)
            scene.render.image_settings.file_format = "PNG"
            if not bpy.ops.render.opengl.poll():
                raise HostError("INVALID_CONTEXT", "viewport OpenGL capture is unavailable")
            result = bpy.ops.render.opengl(write_still=True, view_context=True)
            if "FINISHED" not in result:
                raise HostError("HOST_EXCEPTION", "Blender did not complete viewport capture")
            metadata = _metadata(area, region, space, runtime)
        finally:
            scene.render.filepath = old_path
            scene.render.image_settings.file_format = old_format
            space.shading.type = old_shading
            space.overlay.show_overlays = old_overlays

    if not os.path.exists(output_path):
        raise HostError("HOST_EXCEPTION", "viewport capture completed but no artifact was written")
    artifact = {
        "kind": "image",
        "mime": "image/png",
        "path": str(output_path),
        "bytes": os.path.getsize(output_path),
    }
    size = _png_size(output_path)
    if size is not None:
        artifact.update(size)
        metadata["image"] = size
    return {"artifact": artifact, "view": metadata}


def register(registry) -> None:
    registry.add("viewport.inspect", inspect, requires_ui=True, stability="beta")
    registry.add("viewport.focus", focus, requires_ui=True, stability="alpha")
    registry.add("viewport.axis", axis, requires_ui=True, stability="alpha")
    registry.add("viewport.capture", capture, evidence=True, requires_ui=True, stability="alpha")
