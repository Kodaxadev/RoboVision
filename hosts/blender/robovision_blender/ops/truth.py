"""The measurement verbs an Artist Loop compares Q0 and Q1 with.

Reads, all of them, and authoritative ones: a measurement stamped with the
revision of the state before it would be worse than no measurement, because the
whole mechanic is putting two of them beside each other and believing the
difference.

`truth.measure` is the one a loop calls. The individual kinds are published too,
because an agent narrowing down a problem should not have to pay for a
self-intersection search to re-read a bounding box.
"""
from __future__ import annotations

from typing import Any

from ..registry import AUTHORITATIVE, INDEPENDENT, HostError
from ..truth import (coverage, evaluate as evaluation, geometry, locality, pattern,
                     reference, spatial, views)
from ..truth.certificate import comparable

KINDS = ("geometry", "spatial")
# Coverage is not in the default bundle. It casts a ray per sample per candidate
# camera, which is worth paying for deliberately and wrong to charge every
# caller who only wanted to know whether the mesh is manifold.
ALL_KINDS = KINDS + ("coverage",)
# Reference comparison is not in the bundle at all: it needs a reference image
# and a claim about which canonical view that image corresponds to, neither of
# which a generic "measure this" call could supply.


def _epsilon(params: dict[str, Any]) -> float:
    try:
        value = float(params.get("epsilon", 1e-10))
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", "epsilon must be a number") from exc
    if value <= 0.0:
        raise HostError("INVALID_PARAMS", "epsilon must be positive")
    return value


def _max_dimension(params: dict[str, Any]) -> float | None:
    value = params.get("max_dimension")
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", "max_dimension must be a number") from exc
    if value <= 0.0:
        raise HostError("INVALID_PARAMS", "max_dimension must be positive")
    return value


def geometry_truth(params, runtime):
    objects, intentional_open = geometry.resolve_subjects(params)
    return geometry.measure(
        objects,
        intentional_open=intentional_open,
        epsilon=_epsilon(params),
        # The overlap search is the only part of this that is not cheap, so it is
        # opt-out rather than always paid for. It defaults on: a loop that
        # forgot to ask should get the stricter answer, not the faster one.
        check_intersections=bool(params.get("check_intersections", True)),
        runtime=runtime,
    ).certificate()


def spatial_truth(params, runtime):
    objects, _ = geometry.resolve_subjects(params)
    return spatial.measure(objects, max_dimension=_max_dimension(params),
                           runtime=runtime).certificate()


def _coverage_options(params: dict[str, Any]) -> dict[str, Any]:
    level = params.get("level", 1)
    if level not in views.ICOSPHERE_LEVELS:
        raise HostError("INVALID_PARAMS",
                        f"level must be one of {sorted(views.ICOSPHERE_LEVELS)}",
                        data={"levels": {str(k): v for k, v in views.ICOSPHERE_LEVELS.items()}})
    projection = str(params.get("projection", views.ORTHOGRAPHIC))
    if projection not in views.PROJECTIONS:
        raise HostError("INVALID_PARAMS", f"projection must be one of {views.PROJECTIONS}")
    selected = params.get("views")
    if selected is not None and (not isinstance(selected, list)
                                 or any(not isinstance(v, str) for v in selected)):
        raise HostError("INVALID_PARAMS", "views must be an array of view ids")
    minimum = params.get("min_coverage")
    if minimum is not None:
        minimum = float(minimum)
        if not 0.0 < minimum <= 1.0:
            raise HostError("INVALID_PARAMS", "min_coverage must be within (0, 1]")
    budget = int(params.get("samples", 4096))
    if not 64 <= budget <= 65536:
        raise HostError("INVALID_PARAMS", "samples must be between 64 and 65536")
    return {
        "level": level, "projection": projection, "selected": selected,
        "min_coverage": minimum, "budget": budget,
        "width": int(params.get("width", 512)), "height": int(params.get("height", 512)),
    }


def coverage_truth(params, runtime):
    objects, _ = geometry.resolve_subjects(params)
    try:
        return coverage.measure(objects, runtime=runtime, **_coverage_options(params)).certificate()
    except ValueError as exc:
        raise HostError("INVALID_PARAMS", str(exc)) from exc


def canonical_views_for(params, runtime):
    """The camera set alone, so a caller can render exactly what coverage tested.

    Published separately because the whole point of deriving views from the
    subject is that two different kinds of evidence can be tied to the same
    viewpoint. A renderer that constructed its own cameras "the same way" would
    be a second source of truth about where the observation was taken from.
    """
    objects, _ = geometry.resolve_subjects(params)
    options = _coverage_options(params)
    corners = []
    for obj in objects:
        from mathutils import Vector

        corners.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    frame = views.subject_frame(corners)
    cameras = views.camera_set(frame, level=options["level"],
                               projection=options["projection"],
                               width=options["width"], height=options["height"])
    del runtime
    return {
        "sampler": views.sampler_id(options["level"], options["projection"],
                                    views.FRAMING_MARGIN),
        "frame": frame,
        "count": len(cameras),
        "cameras": cameras,
    }


def reference_truth(params, runtime):
    objects, _ = geometry.resolve_subjects(params)
    options = _coverage_options(params)
    path = params.get("reference")
    view = params.get("view")
    if not isinstance(path, str) or not path:
        raise HostError("INVALID_PARAMS", "reference is required")
    if not isinstance(view, str) or not view:
        raise HostError("INVALID_PARAMS",
                        "view is required: a silhouette comparison is only meaningful "
                        "against a named canonical view",
                        data={"discover": "truth.views"})
    threshold = float(params.get("threshold", 0.5))
    if not 0.0 < threshold <= 1.0:
        raise HostError("INVALID_PARAMS", "threshold must be within (0, 1]")
    use_alpha = params.get("use_alpha")
    if use_alpha is not None and not isinstance(use_alpha, bool):
        raise HostError("INVALID_PARAMS", "use_alpha must be a boolean")
    # `declared` is the only honest value today: nothing here solves for the
    # reference's own camera, and the certificate says so in its limits rather
    # than letting a caller believe the alignment was proved.
    alignment = str(params.get("alignment", "declared"))
    if alignment != "declared":
        raise HostError("INVALID_PARAMS",
                        "only declared alignment is supported; no camera solve exists yet")
    frame = params.get("frame")
    if frame is not None:
        if not isinstance(frame, dict) or "center" not in frame or "radius" not in frame:
            raise HostError("INVALID_PARAMS",
                            "frame must carry center and radius, as returned by truth.views")
        frame = {"center": [float(v) for v in frame["center"]],
                 "radius": float(frame["radius"])}
        if frame["radius"] <= 0.0:
            raise HostError("INVALID_PARAMS", "frame radius must be positive")
    return reference.measure(
        objects, path=path, view=view, level=options["level"],
        projection=options["projection"], threshold=threshold,
        use_alpha=use_alpha, alignment=alignment, frame_override=frame,
        runtime=runtime).certificate()


def pattern_truth(params, runtime):
    members, declaration, subjects = pattern.resolve(params)
    return pattern.measure(members, declaration, runtime, subjects).certificate()


def locality_truth(params, runtime):
    """Compare the current state against a stored snapshot, under a declaration.

    The `before` is a snapshot handle from `scene.snapshot`, so the comparison is
    against the host's own recorded state rather than against a second
    fingerprint this family invented. A correction verified by one notion of
    "changed" and rolled back by another would eventually disagree with itself.
    """
    handle = params.get("before")
    if not isinstance(handle, str) or not handle:
        raise HostError("INVALID_PARAMS",
                        "before must be a snapshot handle from scene.snapshot")
    before = runtime.get_snapshot(handle)
    declaration = locality.resolve(params)
    after = runtime.current_snapshot()
    return locality.measure(before, after, declaration, runtime).certificate()


def evaluate_correction(params, runtime):
    """Decide whether a candidate earned its commit. Arithmetic, not taste."""
    before = params.get("before")
    after = params.get("after")
    for side, value in (("before", before), ("after", after)):
        if not isinstance(value, dict) or not value:
            raise HostError("INVALID_PARAMS",
                            f"{side} must be a map of measurement kind to certificate")
        for kind, certificate in value.items():
            if not isinstance(certificate, dict) or "metrics" not in certificate:
                raise HostError("INVALID_PARAMS",
                                f"{side}[{kind}] is not a measurement certificate")
    del runtime
    return evaluation.evaluate(before, after, evaluation.resolve_policy(params))


def measure(params, runtime):
    """Every requested kind, taken from one authoritative read of one moment.

    Bundled rather than left to the caller to assemble, because two certificates
    fetched separately describe two moments. A human edit between them would put
    a geometry measurement and a dimension measurement on either side of a change
    neither of them mentions.
    """
    kinds = params.get("kinds", list(KINDS))
    if not isinstance(kinds, list) or not kinds:
        raise HostError("INVALID_PARAMS", "kinds must be a non-empty array")
    unknown = [kind for kind in kinds if kind not in ALL_KINDS]
    if unknown:
        raise HostError("INVALID_PARAMS", f"unknown measurement kinds: {unknown}",
                        data={"supported": list(ALL_KINDS)})

    certificates: dict[str, Any] = {}
    if "geometry" in kinds:
        certificates["geometry"] = geometry_truth(params, runtime)
    if "spatial" in kinds:
        certificates["spatial"] = spatial_truth(params, runtime)
    if "coverage" in kinds:
        certificates["coverage"] = coverage_truth(params, runtime)

    holds = all(entry["invariants_hold"] for entry in certificates.values())
    unmeasured = {name: reason for entry in certificates.values()
                  for name, reason in entry["limits"].items()}
    return {
        "kinds": sorted(certificates),
        "certificates": certificates,
        # The one aggregate worth having, and it is a gate rather than a score.
        # No overall quality number: a single figure is exactly what would let a
        # loop trade an invariant for an improvement.
        "invariants_hold": holds,
        "failing_invariants": sorted(
            name for entry in certificates.values()
            for name, invariant in entry["invariants"].items() if not invariant["holds"]),
        "unmeasured": unmeasured,
    }


def compare(params, runtime):
    """Two certificates, and exactly what moved between them.

    The refusal matters more than the arithmetic. A Q0 and a Q1 from different
    worlds, different coordinate contracts or different subjects are not a before
    and an after, and computing a confident delta over them would be the most
    convincing wrong answer this system could produce.
    """
    before = params.get("before")
    after = params.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise HostError("INVALID_PARAMS", "before and after must be certificates")
    for side, value in (("before", before), ("after", after)):
        if "metrics" not in value or "pins" not in value:
            raise HostError("INVALID_PARAMS", f"{side} is not a measurement certificate")

    ok, reason = comparable(before, after)
    if not ok:
        raise HostError(
            "INCOMPARABLE_MEASUREMENTS",
            "those two certificates do not describe the same thing measured twice",
            data={"reason": reason,
                  "before_pins": before["pins"], "after_pins": after["pins"]},
        )

    deltas: dict[str, Any] = {}
    for name, entry in after["metrics"].items():
        if name not in before["metrics"]:
            deltas[name] = {"delta": None, "reason": "not_measured_before"}
            continue
        was = before["metrics"][name]["value"]
        now = entry["value"]
        change = now - was
        direction = entry["direction"]
        if change == 0:
            movement = "unchanged"
        elif direction == "neutral":
            movement = "changed"
        else:
            better = change < 0 if direction == "lower_better" else change > 0
            movement = "improved" if better else "regressed"
        deltas[name] = {"before": was, "after": now, "delta": round(change, 9),
                        "direction": direction, "movement": movement}

    broke = sorted(name for name, entry in after["invariants"].items()
                   if not entry["holds"] and before["invariants"].get(name, {}).get("holds"))
    fixed = sorted(name for name, entry in after["invariants"].items()
                   if entry["holds"] and not before["invariants"].get(name, {"holds": True})["holds"])
    del runtime
    return {
        "identical": before["certificate"] == after["certificate"],
        "metrics": dict(sorted(deltas.items())),
        "improved": sorted(n for n, d in deltas.items() if d.get("movement") == "improved"),
        "regressed": sorted(n for n, d in deltas.items() if d.get("movement") == "regressed"),
        "changed": sorted(n for n, d in deltas.items() if d.get("movement") == "changed"),
        "invariants_broken": broke,
        "invariants_fixed": fixed,
        "invariants_hold": after["invariants_hold"],
    }


def register(registry) -> None:
    # Authoritative on purpose. A measurement carrying the revision of the state
    # before it is the one failure mode that would poison every comparison built
    # on it, and this whole family exists to be compared.
    registry.add("truth.measure", measure, reads=AUTHORITATIVE, stability="alpha")
    registry.add("truth.geometry", geometry_truth, reads=AUTHORITATIVE, stability="alpha")
    registry.add("truth.spatial", spatial_truth, reads=AUTHORITATIVE, stability="alpha")
    registry.add("truth.coverage", coverage_truth, reads=AUTHORITATIVE, stability="alpha")
    # The camera contract is pure geometry derived from the subject's bounds and
    # does not read scene state beyond the subjects it was given, but it is
    # classified with the rest of the family rather than talked into a cheaper
    # class: it resolves objects, and an answer about a subject that has been
    # deleted is not a cheaper answer, it is a wrong one.
    registry.add("truth.views", canonical_views_for, reads=AUTHORITATIVE, stability="alpha")
    registry.add("truth.reference", reference_truth, reads=AUTHORITATIVE, stability="alpha")
    registry.add("truth.pattern", pattern_truth, reads=AUTHORITATIVE, stability="alpha")
    registry.add("truth.locality", locality_truth, reads=AUTHORITATIVE, stability="alpha")
    # Pure arithmetic over two certificates the caller already holds: it does not
    # look at the scene at all, and must not, or it would be measuring a third
    # moment while claiming to compare two.
    registry.add("truth.compare", compare, reads=INDEPENDENT, stability="alpha")
    # The evaluator is arithmetic over certificates the caller already holds. It
    # must not read the scene: a decision that consulted a third moment while
    # claiming to judge two would be the one place a wrong answer gets committed.
    registry.add("truth.evaluate", evaluate_correction, reads=INDEPENDENT,
                 stability="alpha")
