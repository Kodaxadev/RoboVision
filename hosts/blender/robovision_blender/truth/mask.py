"""Binary masks, and the distances between them.

A silhouette is geometric occupancy: which pixels of a given camera's frame the
asset occupies. That is a question about geometry, so it is answered by casting a
ray per pixel against the same BVH coverage uses, through the same canonical
camera matrices. No shading, no anti-aliasing, no render pipeline, and no
graphics context — which means the answer is identical in headless CI, in a
background editor and on a machine with no GPU, and it cannot drift when a
renderer changes its defaults.

A shaded render of the same camera is still worth having for a human to look at.
It is not what a metric is computed from, because then the metric would be partly
about the renderer.

The comparisons here are deliberately several. Intersection-over-union answers
"is this roughly the right amount of material in roughly the right place" and is
blind to shape: two quite different outlines can preserve a lot of overlap. A
contour distance answers "is the outline in the right place" and is blind to
which direction it is wrong in. So both are computed, plus a per-sector
excess/deficit that says *where* and *which way* — because "IoU = 0.73" tells a
model it has a problem and nothing about what to do next.
"""
from __future__ import annotations

import hashlib
from typing import Any

from mathutils import Vector

MASK_SCHEMA = 1

# Eight sectors around the reference centroid. Enough to separate "the back is
# too deep" from "the roof is too tall" without pretending to a precision the
# mask resolution does not support.
SECTORS = 8
SECTOR_NAMES = ("right", "upper_right", "top", "upper_left",
                "left", "lower_left", "bottom", "lower_right")


def silhouette(tree, camera: dict[str, Any], frame: dict[str, Any]) -> list[bool]:
    """Which pixels of this canonical camera the subjects occupy.

    Ray per pixel, in the camera's own frame, so the mask is exactly aligned to
    the matrices recorded in the view contract rather than approximately aligned
    to something reconstructed later.
    """
    width, height = camera["width"], camera["height"]
    position = Vector(camera["position"])
    direction = Vector(camera["direction"])
    up = Vector(camera["up"])
    right = direction.cross(up).normalized() * -1.0
    radius = float(frame["radius"])
    reach = radius * 12.0

    mask = [False] * (width * height)
    if camera["projection"] == "orthographic":
        extent = float(camera["orthographic_extent"])
        aspect = width / height
        for y in range(height):
            # +1 at the top so the mask reads the way an image does.
            ndc_y = 1.0 - 2.0 * (y + 0.5) / height
            for x in range(width):
                ndc_x = 2.0 * (x + 0.5) / width - 1.0
                origin = (position
                          + right * (ndc_x * extent * aspect)
                          + up * (ndc_y * extent))
                hit = tree.ray_cast(origin, -direction, reach)
                mask[y * width + x] = hit[0] is not None
        return mask

    from math import tan

    focal = tan(float(camera["field_of_view"]) / 2.0)
    aspect = width / height
    for y in range(height):
        ndc_y = 1.0 - 2.0 * (y + 0.5) / height
        for x in range(width):
            ndc_x = 2.0 * (x + 0.5) / width - 1.0
            ray = (-direction
                   + right * (ndc_x * focal * aspect)
                   + up * (ndc_y * focal)).normalized()
            hit = tree.ray_cast(position, ray, reach)
            mask[y * width + x] = hit[0] is not None
    return mask


def from_image(pixels: list[float], width: int, height: int, *,
               threshold: float, use_alpha: bool) -> list[bool]:
    """Threshold a loaded image into foreground/background.

    Alpha where the reference has it, luminance otherwise. Which one was used is
    recorded with the measurement: a reference cut out against transparency and
    one drawn on white are different inputs, and a mask that did not say which it
    read would make two incomparable references look interchangeable.
    """
    mask = [False] * (width * height)
    for index in range(width * height):
        base = index * 4
        if use_alpha:
            value = pixels[base + 3]
        else:
            r, g, b = pixels[base], pixels[base + 1], pixels[base + 2]
            # Luminance, then inverted: artwork is usually dark on light, so the
            # subject is what is *not* background.
            value = 1.0 - (0.2126 * r + 0.7152 * g + 0.0722 * b)
        mask[index] = value >= threshold
    # Blender hands back rows bottom-up; images are read top-down.
    flipped = [False] * (width * height)
    for y in range(height):
        source = (height - 1 - y) * width
        flipped[y * width:(y + 1) * width] = mask[source:source + width]
    return flipped


def content_hash(data: bytes) -> str:
    return "rvref:" + hashlib.sha256(data).hexdigest()[:32]


def area(mask: list[bool]) -> int:
    return sum(1 for value in mask if value)


def iou(a: list[bool], b: list[bool]) -> tuple[float, int, int]:
    """Overlap over union, plus the two one-sided errors it hides.

    Returned together on purpose: a model told only the ratio cannot tell whether
    it has too much material or too little, and those call for opposite
    corrections.
    """
    intersection = 0
    union = 0
    excess = 0
    deficit = 0
    for left, right in zip(a, b):
        if left or right:
            union += 1
        if left and right:
            intersection += 1
        elif left:
            excess += 1
        elif right:
            deficit += 1
    return (intersection / union if union else 1.0), excess, deficit


def contour(mask: list[bool], width: int, height: int) -> list[int]:
    """Foreground pixels with a background neighbour — the outline itself."""
    edge = []
    for y in range(height):
        row = y * width
        for x in range(width):
            index = row + x
            if not mask[index]:
                continue
            if (x == 0 or y == 0 or x == width - 1 or y == height - 1
                    or not mask[index - 1] or not mask[index + 1]
                    or not mask[index - width] or not mask[index + width]):
                edge.append(index)
    return edge


def _edt_1d(values: list[float], length: int) -> list[float]:
    """Exact squared distance transform of one line (Felzenszwalb).

    Exact rather than an approximate chamfer mask, because an approximation
    biases diagonals and the whole point of this number is that a contour error
    of the same size reads the same wherever on the outline it happens.
    """
    out = [0.0] * length
    vertices = [0] * length
    boundaries = [0.0] * (length + 1)
    k = 0
    vertices[0] = 0
    boundaries[0] = -1e20
    boundaries[1] = 1e20
    for q in range(1, length):
        while True:
            v = vertices[k]
            s = ((values[q] + q * q) - (values[v] + v * v)) / (2.0 * q - 2.0 * v)
            if s <= boundaries[k]:
                k -= 1
                continue
            break
        k += 1
        vertices[k] = q
        boundaries[k] = s
        boundaries[k + 1] = 1e20
    k = 0
    for q in range(length):
        while boundaries[k + 1] < q:
            k += 1
        v = vertices[k]
        out[q] = (q - v) * (q - v) + values[v]
    return out


def distance_field(edge: list[int], width: int, height: int) -> list[float]:
    """Distance in pixels from every position to the nearest contour pixel."""
    infinity = 1e20
    grid = [infinity] * (width * height)
    for index in edge:
        grid[index] = 0.0
    for y in range(height):
        row = grid[y * width:(y + 1) * width]
        grid[y * width:(y + 1) * width] = _edt_1d(row, width)
    for x in range(width):
        column = [grid[y * width + x] for y in range(height)]
        column = _edt_1d(column, height)
        for y in range(height):
            grid[y * width + x] = column[y]
    return [value ** 0.5 for value in grid]


def chamfer(a: list[int], b: list[int], width: int, height: int) -> dict[str, float]:
    """Symmetric mean contour distance, and the worst single excursion.

    Symmetric because a one-sided measure can be made small by shrinking the
    outline it measures from: every point of a tiny blob can sit close to a large
    reference contour while the reference is nowhere near the blob.
    """
    if not a or not b:
        return {"mean": float("inf"), "max": float("inf"),
                "subject_to_reference": float("inf"),
                "reference_to_subject": float("inf")}
    to_b = distance_field(b, width, height)
    to_a = distance_field(a, width, height)
    forward = [to_b[index] for index in a]
    backward = [to_a[index] for index in b]
    return {
        "subject_to_reference": sum(forward) / len(forward),
        "reference_to_subject": sum(backward) / len(backward),
        "mean": (sum(forward) / len(forward) + sum(backward) / len(backward)) / 2.0,
        "max": max(max(forward), max(backward)),
    }


def sectors(subject: list[bool], reference: list[bool], width: int,
            height: int) -> list[dict[str, Any]]:
    """Where the outline is wrong, and in which direction.

    Around the reference's own centroid rather than the image centre, so a
    subject drawn off to one side does not report every sector as wrong. The
    output is the actionable half of this whole measurement: "the lower-left is
    9.4% over" is something a model can turn into an edit; a single ratio is not.
    """
    from math import atan2, pi

    total = area(reference) or 1
    cx = sum(index % width for index, on in enumerate(reference) if on) / total
    cy = sum(index // width for index, on in enumerate(reference) if on) / total

    buckets = [{"excess": 0, "deficit": 0, "reference": 0} for _ in range(SECTORS)]
    for index, (left, right) in enumerate(zip(subject, reference)):
        if not left and not right:
            continue
        dx = (index % width) - cx
        # Image y grows downward; flip it so "top" means what a viewer means.
        dy = cy - (index // width)
        angle = atan2(dy, dx)
        bucket = int(((angle + 2 * pi) % (2 * pi)) / (2 * pi) * SECTORS) % SECTORS
        if right:
            buckets[bucket]["reference"] += 1
        if left and not right:
            buckets[bucket]["excess"] += 1
        elif right and not left:
            buckets[bucket]["deficit"] += 1

    out = []
    for index, bucket in enumerate(buckets):
        denominator = bucket["reference"] or 1
        out.append({
            "sector": SECTOR_NAMES[index],
            "excess_pixels": bucket["excess"],
            "deficit_pixels": bucket["deficit"],
            "reference_pixels": bucket["reference"],
            "excess_fraction": round(bucket["excess"] / denominator, 6),
            "deficit_fraction": round(bucket["deficit"] / denominator, 6),
            "net_fraction": round((bucket["excess"] - bucket["deficit"]) / denominator, 6),
        })
    return out


def bounds(mask: list[bool], width: int, height: int) -> dict[str, Any] | None:
    """The mask's own extent, for proportion comparisons IoU cannot make."""
    xs = [index % width for index, on in enumerate(mask) if on]
    ys = [index // width for index, on in enumerate(mask) if on]
    if not xs:
        return None
    span_x = max(xs) - min(xs) + 1
    span_y = max(ys) - min(ys) + 1
    return {
        "min_x": min(xs), "max_x": max(xs), "min_y": min(ys), "max_y": max(ys),
        "width": span_x, "height": span_y,
        "aspect": round(span_x / span_y, 6),
        "fill": round(area(mask) / (span_x * span_y), 6),
    }
