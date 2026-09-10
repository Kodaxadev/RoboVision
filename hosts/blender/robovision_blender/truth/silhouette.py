"""Write a subject's geometric silhouette from a canonical view to an image.

The counterpart of `truth.reference`, and it exists because that comparison had
no way to be set up from outside. A client can measure a silhouette against a
reference image; nothing could *produce* one, so any workflow that wanted to
capture a blockout profile, record what the measurement actually sees, or show a
human two states side by side had to reach into Blender directly — which is
exactly the private path the Artist Loop is meant to prove unnecessary.

Geometric occupancy, raycast through the same canonical camera the coverage and
reference measurements use. It carries no shading, no materials and no lighting,
and the artifact record says so: this is what the measurement sees, not what the
asset looks like.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy
from mathutils.bvhtree import BVHTree

from ..identity import object_id
from ..registry import HostError
from . import mask as masks
from . import views as canonical_views
from .reference import _triangles


def render(objects: list, *, path: str | None, view: str, level: int,
           projection: str, width: int, height: int,
           frame_override: dict[str, Any] | None) -> dict[str, Any]:
    """One view, one image, and the provenance tying them together."""
    points, indices, corners = _triangles(objects)
    if not indices:
        raise HostError("INVALID_PARAMS", "the subjects have no geometry to render")

    frame = frame_override or canonical_views.subject_frame(corners)
    cameras = canonical_views.camera_set(frame, level=level, projection=projection,
                                         width=width, height=height)
    camera = next((entry for entry in cameras if entry["view"] == view), None)
    if camera is None:
        raise HostError("INVALID_PARAMS", f"unknown view id for this sampler: {view}",
                        data={"count": len(cameras)})

    tree = BVHTree.FromPolygons(points, indices, all_triangles=True)
    occupancy = masks.silhouette(tree, camera, frame)

    if path is None:
        import tempfile
        import uuid

        directory = Path(tempfile.gettempdir()) / "robovision"
        directory.mkdir(parents=True, exist_ok=True)
        output = directory / f"silhouette-{view}-{uuid.uuid4().hex[:8]}.png"
    else:
        output = Path(str(path)).expanduser().resolve()
        if output.suffix.lower() != ".png":
            raise HostError("INVALID_PARAMS", "silhouette path must end in .png")
        output.parent.mkdir(parents=True, exist_ok=True)

    image = bpy.data.images.new(f"rv-silhouette-{view}", width=width, height=height,
                                alpha=True)
    try:
        pixels = [0.0] * (width * height * 4)
        for y in range(height):
            # Blender stores rows bottom-up; the mask is top-down.
            source = (height - 1 - y) * width
            for x in range(width):
                on = 1.0 if occupancy[source + x] else 0.0
                base = (y * width + x) * 4
                pixels[base:base + 4] = [on, on, on, on]
        image.pixels = pixels
        image.filepath_raw = str(output)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)

    covered = masks.area(occupancy)
    return {
        "artifact": {"kind": "image", "mime": "image/png", "path": str(output),
                     "bytes": output.stat().st_size},
        # What the image actually depicts. An image cannot say this about itself,
        # and a comparison against a reference drawn over a different set of parts
        # reads the difference as shape error — measured, on the first Artist Loop
        # run, where a reference rendered with one extra object made the subject
        # look 59% too narrow and sent a correction after the wrong part.
        "subjects": sorted(object_id(obj) for obj in objects),
        "subject_names": sorted(obj.name for obj in objects),
        "view": camera,
        "frame": frame,
        "sampler": canonical_views.sampler_id(level, projection,
                                              canonical_views.FRAMING_MARGIN),
        "framing": "declared" if frame_override else "subject_relative",
        "occupied_pixels": covered,
        "occupied_fraction": round(covered / (width * height), 6),
        "content": "projected geometric occupancy",
        "note": "raycast through the canonical camera; carries no shading, materials "
                "or lighting, and is not a render of the asset's appearance",
    }
