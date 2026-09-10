"""Turning a pattern's geometry into metrics and gates, which are different things.

Split from the pattern measurement itself because this is where the one real
judgement lives: which parts of "is this array correct" are binary and which are
a gradient a correction can move.

Count is binary — seven fins where eight were required is a missing fin, not a
fin that needs nudging. Everything else here is a quality gradient with a
*declared* tolerance, and where no tolerance was declared the property is
reported as measured but ungated rather than silently held to a number nobody
asked for. That distinction matters more here than almost anywhere else in the
system: an array that is imperceptibly uneven is usually finished work, and a
validator that failed it would be turned off.
"""
from __future__ import annotations

from math import acos, degrees
from typing import Any

from .certificate import LOWER_BETTER, NEUTRAL, Measurement


def statistics(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "cv": 0.0}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    deviation = variance ** 0.5
    return {"mean": mean, "min": min(values), "max": max(values),
            # Coefficient of variation rather than raw spread, so the same
            # relative inconsistency reads the same on a 6mm fin and a 6m wing.
            "cv": deviation / abs(mean) if abs(mean) > 1e-12 else 0.0}


def record_spacing(measurement: Measurement, gaps: list[float], target: float | None,
                   unit: str, declaration: dict[str, Any]) -> None:
    """How even the array is, and how far it is from the pitch that was asked for.

    Those are two different failures. An array can be perfectly even at the wrong
    pitch, or average out to the right pitch while one member sits visibly out of
    line, and a single number would hide whichever one is happening.
    """
    stats = statistics(gaps)
    measurement.metric("pattern.spacing_mean", round(stats["mean"], 9),
                       direction=NEUTRAL, unit=unit)
    measurement.metric("pattern.spacing_cv", round(stats["cv"], 6),
                       direction=LOWER_BETTER, unit="ratio")

    if target is None:
        errors = [abs(gap - stats["mean"]) for gap in gaps]
        measurement.unmeasured("pattern.spacing_matches_declared",
                               "no spacing was declared; only evenness was measured")
    else:
        errors = [abs(gap - target) for gap in gaps]

    worst = max(errors)
    measurement.metric("pattern.spacing_mean_error",
                       round(sum(errors) / len(errors), 9),
                       direction=LOWER_BETTER, unit=unit)
    measurement.metric("pattern.spacing_max_error", round(worst, 9),
                       direction=LOWER_BETTER, unit=unit,
                       # Which gap, so a correction knows where to look rather
                       # than being told the array is 7mm wrong somewhere.
                       worst_gap_index=errors.index(worst))

    tolerance = declaration.get("spacing_tolerance")
    if tolerance is None:
        measurement.unmeasured("pattern.spacing_within_tolerance",
                               "no spacing_tolerance was declared")
    else:
        measurement.invariant("pattern.spacing_within_tolerance", worst <= tolerance,
                              max_error=round(worst, 9), tolerance=tolerance, unit=unit)
    if target is not None:
        measurement.invariant(
            "pattern.spacing_matches_declared",
            worst <= (tolerance if tolerance is not None else 1e-9),
            declared=target, max_error=round(worst, 9))


def record_orientation(measurement: Measurement, members: list[dict[str, Any]],
                       declaration: dict[str, Any]) -> None:
    """How far any member has turned relative to its siblings."""
    baseline = members[0]["axis"]
    deviations = []
    for member in members[1:]:
        # Absolute dot: an axis has no sign, and without this two identical
        # members could report a 180 degree disagreement.
        dot = max(-1.0, min(1.0, abs(baseline.dot(member["axis"]))))
        deviations.append(degrees(acos(dot)))

    worst = max(deviations) if deviations else 0.0
    measurement.metric(
        "pattern.max_angular_deviation", round(worst, 6),
        direction=LOWER_BETTER, unit="degree",
        worst_member=members[1 + deviations.index(worst)]["member"] if deviations else None)

    tolerance = declaration.get("orientation_tolerance")
    if tolerance is None:
        measurement.unmeasured("pattern.orientation_within_tolerance",
                               "no orientation_tolerance was declared")
    else:
        measurement.invariant("pattern.orientation_within_tolerance", worst <= tolerance,
                              max_deviation=round(worst, 6), tolerance=tolerance)


def record_dimensions(measurement: Measurement, members: list[dict[str, Any]],
                      declaration: dict[str, Any]) -> None:
    """Whether the members are the same size as each other."""
    extents = [member["extent"] for member in members]
    stats = statistics(extents)
    measurement.metric("pattern.dimension_cv", round(stats["cv"], 6),
                       direction=LOWER_BETTER, unit="ratio")
    measurement.metric("pattern.dimension_spread", round(stats["max"] - stats["min"], 9),
                       direction=LOWER_BETTER, unit="metre")

    limit = declaration.get("dimension_variance")
    if limit is None:
        measurement.unmeasured("pattern.dimensions_consistent",
                               "no dimension_variance was declared")
    else:
        measurement.invariant("pattern.dimensions_consistent", stats["cv"] <= limit,
                              coefficient_of_variation=round(stats["cv"], 6), limit=limit)
