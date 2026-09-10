"""Where the unobserved surface is, and which camera would reveal most of it.

Split from the coverage measurement because these answer the question a model
actually asks next. "18.2% unobserved" says there is work to do; "an unobserved
region of 0.31 square metres centred here, and view v005 would reveal 42% of the
surface" says what to do about it.

Both are calculations rather than suggestions. A model handed a list of
forty-two directions and a prose description of what is missing would be
guessing at something the system can work out exactly, and its guess would not
be reproducible between runs.
"""
from __future__ import annotations

from typing import Any

from mathutils import Vector

from .certificate import HIGHER_BETTER, LOWER_BETTER, NEUTRAL, Measurement


def regions(samples: list[dict[str, Any]], unobserved: set[int],
             faces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group unobserved samples into surface regions an agent can act on.

    "18.2% unobserved" tells a model that it has work to do; "an unobserved
    region of 0.31 square metres centred here" tells it where to point the next
    camera. Grouped by proximity on a grid scaled to the region size, which is
    approximate and says so rather than pretending to be surface connectivity:
    two patches on either side of a thin wall can land in one cell.
    """
    if not unobserved:
        return []
    positions = [samples[index]["position"] for index in unobserved]
    extent = max(
        max(p[axis] for p in positions) - min(p[axis] for p in positions)
        for axis in range(3)
    ) or 1.0
    cell = extent / 12.0 or 1.0

    buckets: dict[tuple[int, int, int], dict[str, Any]] = {}
    for index in sorted(unobserved):
        sample = samples[index]
        key = tuple(int(sample["position"][axis] // cell) for axis in range(3))
        bucket = buckets.setdefault(key, {"area": 0.0, "samples": 0,
                                          "centroid": Vector((0.0, 0.0, 0.0)),
                                          "objects": set(), "faces": set()})
        bucket["area"] += sample["area"]
        bucket["samples"] += 1
        bucket["centroid"] = bucket["centroid"] + sample["position"]
        bucket["objects"].add(sample["object"])
        bucket["faces"].add(faces[sample["face"]]["source_face"])

    # Adjacent cells are one region. Reporting the raw grid instead produced 439
    # "regions" for a single concealed underside — technically a partition, and
    # useless to a model deciding where to point a camera. Merging by
    # 6-connectivity gives back the thing an agent can act on: one patch, its
    # area, and where it is.
    parent = {key: key for key in buckets}

    def find(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    for key in sorted(buckets):
        for axis in range(3):
            neighbour = list(key)
            neighbour[axis] += 1
            neighbour = tuple(neighbour)
            if neighbour in parent:
                a, b = find(key), find(neighbour)
                if a != b:
                    parent[b] = a

    merged: dict[tuple[int, int, int], dict[str, Any]] = {}
    for key, bucket in sorted(buckets.items()):
        root = find(key)
        target = merged.setdefault(root, {"area": 0.0, "samples": 0,
                                          "centroid": Vector((0.0, 0.0, 0.0)),
                                          "objects": set(), "faces": set(),
                                          "cells": 0})
        target["area"] += bucket["area"]
        target["samples"] += bucket["samples"]
        target["centroid"] = target["centroid"] + bucket["centroid"]
        target["objects"] |= bucket["objects"]
        target["faces"] |= bucket["faces"]
        target["cells"] += 1

    regions = []
    for bucket in merged.values():
        centroid = bucket["centroid"] / bucket["samples"]
        regions.append({
            "area": round(bucket["area"], 9),
            "samples": bucket["samples"],
            "cells": bucket["cells"],
            "centroid": [round(v, 9) for v in centroid],
            "objects": sorted(bucket["objects"]),
            "example_faces": sorted(bucket["faces"])[:16],
        })
    regions.sort(key=lambda region: (-region["area"], region["centroid"]))
    return regions


def next_view(seen_by: dict[str, set[int]], observed: set[int],
              samples: list[dict[str, Any]], by_id: dict[str, dict[str, Any]],
              chosen: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The candidate camera that would reveal the most unobserved surface.

    Calculated, not asked for. A model handed a list of forty-two directions and
    a prose description of what is missing would be guessing at something the
    system can work out exactly, and its guess would not be reproducible.

    Ties broken by view id so the recommendation is stable across runs.
    """
    used = {camera["view"] for camera in chosen}
    best: tuple[float, str] | None = None
    for view, visible in sorted(seen_by.items()):
        if view in used:
            continue
        gain = sum(samples[index]["area"] for index in visible - observed)
        if gain <= 0.0:
            continue
        if best is None or gain > best[0]:
            best = (gain, view)
    if best is None:
        return None
    gain, view = best
    total = sum(sample["area"] for sample in samples)
    return {
        "view": view,
        "direction": by_id[view]["direction"],
        "camera": by_id[view],
        "predicted_new_area": round(gain, 9),
        "predicted_new_fraction": round(gain / total, 6),
        "basis": "maximises newly observed surface area among unused candidates",
    }


def record(measurement: Measurement, observed_area: float, total_area: float,
            view_count: int, regions: list[dict[str, Any]],
            min_coverage: float | None) -> None:
    fraction = observed_area / total_area if total_area else 0.0
    measurement.metric("coverage.observed_fraction", round(fraction, 6),
                       direction=HIGHER_BETTER, unit="fraction")
    measurement.metric("coverage.unobserved_fraction", round(1.0 - fraction, 6),
                       direction=LOWER_BETTER, unit="fraction")
    measurement.metric("coverage.unobserved_area", round(total_area - observed_area, 9),
                       direction=LOWER_BETTER, unit="square_metre")
    measurement.metric("coverage.largest_unobserved_region_area",
                       round(regions[0]["area"], 9) if regions else 0.0,
                       direction=LOWER_BETTER, unit="square_metre")
    measurement.metric("coverage.unobserved_regions", len(regions), direction=LOWER_BETTER)
    measurement.metric("coverage.views_used", view_count, direction=NEUTRAL)

    if min_coverage is None:
        # A coverage requirement is a declaration about how thoroughly this
        # particular asset must be inspected, not a property of geometry.
        measurement.unmeasured("coverage.meets_declared_minimum",
                               "no min_coverage declared")
    else:
        measurement.invariant("coverage.meets_declared_minimum",
                              fraction >= min_coverage,
                              observed_fraction=round(fraction, 6),
                              min_coverage=min_coverage)
