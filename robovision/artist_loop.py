"""The orchestration around a correction, with none of the judgement inside it.

The division of labour is the whole design. RoboVision supplies current-state
truth, exact targeting, reversible execution, locality enforcement and the
accept/reject arithmetic. The model interprets the brief, decides what looks
wrong, picks one objective, and chooses the operations. Nothing in this file
decides what to correct, and nothing in it knows which model is asking — a
correction is a JSON object any reasoning client can emit, and the evidence it
reads is the same shape whoever consumes it.

That constraint is not decoration. If the driver ranked discrepancies and then
"the model" acted on the ranking, the experiment would be measuring the driver.
So the deterministic ranking here is recorded as *advisory* and never consulted:
it exists so that later we can ask whether models chose better or worse than a
trivial priority order would have.

One honesty rule runs through the evidence. Coverage and reference agreement are
computed by raycasting geometry — projected geometric occupancy and sampled
surface visibility. They say nothing about shading, materials, lighting or
texture, and the packet labels them so no reader can mistake a silhouette match
for a rendered likeness.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .session import HostSession

LOOP_SCHEMA = 1


@dataclass
class Brief:
    """What the asset is meant to be, declared once and never inferred."""

    name: str
    subjects: list[str]
    # Each: {"key": "front", "path": ..., "view": ..., "frame": {...}}
    references: list[dict[str, Any]] = field(default_factory=list)
    # Each: {"key": "fins", "params": {...as truth.pattern...}}
    patterns: list[dict[str, Any]] = field(default_factory=list)
    protected_objects: list[str] = field(default_factory=list)
    required_invariants: Any = "all"
    coverage_level: int = 1
    coverage_samples: int = 2048
    max_dimension: float | None = None
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name, "subjects": self.subjects,
            "references": [{k: v for k, v in ref.items() if k != "frame"}
                           for ref in self.references],
            "patterns": self.patterns, "protected_objects": self.protected_objects,
            "required_invariants": self.required_invariants,
            "max_dimension": self.max_dimension, "notes": self.notes,
        }


def measure(session: HostSession, brief: Brief) -> dict[str, Any]:
    """One measurement bundle: every kind the brief declares, at one moment.

    Keys become the `kind` a policy names, which is how a front-view and a
    side-view reference metric of the same name stay apart.
    """
    bundle: dict[str, Any] = {}
    # The brief declares the *intended* asset; the measurement covers what exists.
    # Filtering here rather than freezing a list is what lets a restored part join
    # the measurement it belongs to — otherwise a correction could add the missing
    # eighth fin and every subsequent measurement would carry on ignoring it.
    # The measurements are of one declared asset, not of a frozen list of
    # objects. Saying so is what lets a correction that restores a missing part
    # be compared at all — without it every asset-level measurement refuses,
    # because its subject set changed by exactly the part that was added.
    subjects = {"objects": _present(session, brief.subjects),
                "subject_set": f"asset:{brief.name}"}
    bundle["geometry"] = _result(session.call("truth.geometry", dict(subjects)))
    spatial = dict(subjects)
    if brief.max_dimension is not None:
        spatial["max_dimension"] = brief.max_dimension
    bundle["spatial"] = _result(session.call("truth.spatial", spatial))
    bundle["coverage"] = _result(session.call("truth.coverage", {
        **subjects, "level": brief.coverage_level, "samples": brief.coverage_samples}))
    for reference in brief.references:
        request = {**subjects, "reference": reference["path"],
                   "view": reference["view"], "frame": reference["frame"]}
        # Where the brief knows what the reference depicts, the comparison checks
        # it rather than assuming. A reference covering parts the measurement does
        # not is read as subject shape error, silently and convincingly.
        if reference.get("subjects"):
            request["reference_subjects"] = reference["subjects"]
        bundle["ref:" + reference["key"]] = _result(session.call("truth.reference", request))
    for pattern in brief.patterns:
        bundle["pattern:" + pattern["key"]] = _result(
            session.call("truth.pattern", _expand(session, pattern["params"])))
    return bundle


def _present(session: HostSession, names: list[str]) -> list[str]:
    """The declared subjects that currently exist, in declared order."""
    described = _result(session.call("scene.describe"))
    existing = {obj["name"] for obj in described["objects"]}
    return [name for name in names if name in existing]


def _expand(session: HostSession, params: dict[str, Any]) -> dict[str, Any]:
    """Resolve a members prefix against whatever currently exists.

    Mechanical, not a judgement: a declared pattern names how many members there
    should be, and the measurement has to be taken over the members that are
    actually present — otherwise a missing one would fail to resolve and the
    count check, which is the whole point, could never run.
    """
    if "members_prefix" not in params:
        return params
    resolved = dict(params)
    prefix = resolved.pop("members_prefix")
    described = _result(session.call("scene.describe"))
    resolved["members"] = sorted(obj["name"] for obj in described["objects"]
                                 if obj["name"].startswith(prefix))
    return resolved


def _result(response: dict[str, Any]) -> dict[str, Any]:
    return response.get("result") or {}


def _values(certificate: dict[str, Any]) -> dict[str, Any]:
    return {name: entry["value"] for name, entry in certificate.get("metrics", {}).items()}


def _failing(certificate: dict[str, Any]) -> list[str]:
    return sorted(name for name, entry in certificate.get("invariants", {}).items()
                  if not entry["holds"])


def packet(bundle: dict[str, Any], brief: Brief) -> dict[str, Any]:
    """Compact structured evidence — localised, not a metric dump.

    "IoU 0.73" tells a reader there is a problem. "the lower-left of the side
    profile is 9% over" tells it what to do. Everything here that can name a
    place, a direction and a magnitude does.
    """
    geometry = bundle["geometry"]
    spatial = bundle["spatial"]
    coverage = bundle["coverage"]

    references = {}
    for key, certificate in bundle.items():
        if not key.startswith("ref:"):
            continue
        values = _values(certificate)
        worst = certificate["metrics"].get("reference.contour.worst_sector_net", {})
        references[key[4:]] = {
            "silhouette_iou": values.get("reference.macro.silhouette_iou"),
            "excess_fraction": values.get("reference.macro.excess_fraction"),
            "deficit_fraction": values.get("reference.macro.deficit_fraction"),
            "aspect_error": values.get("reference.macro.aspect_error"),
            "contour_mean_distance": values.get("reference.contour.mean_distance"),
            "worst_sector": worst.get("detail", {}).get("sector"),
            "worst_sector_sign": worst.get("detail", {}).get("sign"),
            "worst_sector_magnitude": worst.get("value"),
            "sectors": _sector_summary(certificate),
            "evidence": "projected geometric occupancy; says nothing about shading, "
                        "materials, lighting or texture",
        }

    patterns = {}
    for key, certificate in bundle.items():
        if not key.startswith("pattern:"):
            continue
        values = _values(certificate)
        count = certificate["invariants"].get("pattern.count_matches", {})
        patterns[key[8:]] = {
            "expected_count": count.get("evidence", {}).get("expected"),
            "actual_count": count.get("evidence", {}).get("found"),
            "max_spacing_error": values.get("pattern.spacing_max_error"),
            "worst_gap_index": certificate["metrics"].get(
                "pattern.spacing_max_error", {}).get("detail", {}).get("worst_gap_index"),
            "max_angular_deviation": values.get("pattern.max_angular_deviation"),
            "dimension_cv": values.get("pattern.dimension_cv"),
            "failing": _failing(certificate),
        }

    unobserved = next((s for s in coverage["subjects"]
                       if s["object"] == "__unobserved__"), {"regions": []})
    nxt = next((s for s in coverage["subjects"] if s["object"] == "__next_view__"), None)
    combined = next((s for s in spatial["subjects"] if s["object"] == "__combined__"), {})

    return {
        "schema": LOOP_SCHEMA,
        "asset": {
            "name": brief.name,
            "revision": geometry["pins"]["revision"],
            "world": geometry["pins"]["world_incarnation"],
            "coordinate_contract": geometry["pins"]["coordinate_contract"],
            "subjects": [s["name"] for s in geometry["subjects"] if s.get("name")],
        },
        "hard_invariants": {
            "status": "pass" if geometry["invariants_hold"] else "fail",
            "failing": _failing(geometry),
            "openness": {s["name"]: s["openness"] for s in geometry["subjects"]
                         if s.get("name")},
        },
        "dimensions": {
            "size": combined.get("world_bounds", {}).get("size"),
            "largest": _values(spatial).get("spatial.largest_dimension"),
            "failing": _failing(spatial),
            "unit": "metre",
        },
        "coverage": {
            "observed_fraction": _values(coverage).get("coverage.observed_fraction"),
            "largest_unverified_region": (unobserved["regions"][0]
                                          if unobserved.get("regions") else None),
            "suggested_next_view": {"view": nxt["view"],
                                    "predicted_new_fraction": nxt["predicted_new_fraction"]}
            if nxt else None,
            "evidence": "sampled surface visibility by raycast; not a render",
        },
        "reference": references,
        "patterns": patterns,
        "protected_objects": brief.protected_objects,
        "limits": {kind: certificate.get("limits", {}) for kind, certificate in bundle.items()},
        "certificates": {kind: certificate.get("certificate")
                         for kind, certificate in bundle.items()},
    }


def _sector_summary(certificate: dict[str, Any]) -> list[dict[str, Any]]:
    """The three sectors contributing most error, so a reader can aim at one."""
    subject = next((s for s in certificate["subjects"] if s["object"] == "__sectors__"), None)
    if subject is None:
        return []
    ranked = sorted(subject["sectors"], key=lambda s: -abs(s["net_fraction"]))
    return [{"sector": s["sector"], "net_fraction": s["net_fraction"],
             "excess_fraction": s["excess_fraction"],
             "deficit_fraction": s["deficit_fraction"]}
            for s in ranked[:3] if abs(s["net_fraction"]) > 1e-6]


# Deterministic priority order, recorded and never acted on. It exists so a
# later analysis can ask whether a model chose better than this would have.
def advisory_ranking(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for name in evidence["hard_invariants"]["failing"]:
        ranked.append({"priority": 1, "kind": "failed_hard_invariant", "detail": name})
    for name in evidence["dimensions"]["failing"]:
        ranked.append({"priority": 2, "kind": "requirement_violation", "detail": name})
    for key, pattern in sorted(evidence["patterns"].items()):
        for name in pattern["failing"]:
            ranked.append({"priority": 3, "kind": "pattern_failure",
                           "detail": f"{key}:{name}"})
    for key, reference in sorted(evidence["reference"].items()):
        error = reference.get("contour_mean_distance") or 0.0
        ranked.append({"priority": 4, "kind": "reference_discrepancy",
                       "detail": key, "magnitude": error})
    region = evidence["coverage"]["largest_unverified_region"]
    if region:
        ranked.append({"priority": 5, "kind": "unverified_region",
                       "detail": region["centroid"], "magnitude": region["area"]})
    ranked.sort(key=lambda item: (item["priority"], -(item.get("magnitude") or 0.0)))
    return ranked


# What one unit of each metric is worth, so an epsilon can be checked against the
# measurement's own resolution instead of chosen to make a test pass.
def resolutions(bundle: dict[str, Any]) -> dict[str, float]:
    found: dict[str, float] = {}
    for kind, certificate in bundle.items():
        if kind.startswith("ref:"):
            width = certificate["pins"]["reference"]["width"]
            height = certificate["pins"]["reference"]["height"]
            area = max(1, width * height)
            diagonal = (width * width + height * height) ** 0.5
            found[f"{kind}/reference.macro.silhouette_iou"] = 1.0 / area
            found[f"{kind}/reference.macro.excess_fraction"] = 1.0 / area
            found[f"{kind}/reference.macro.deficit_fraction"] = 1.0 / area
            found[f"{kind}/reference.contour.mean_distance"] = 1.0 / diagonal
            found[f"{kind}/reference.contour.max_distance"] = 1.0 / diagonal
        if kind.startswith("pattern:"):
            # Coordinate precision rather than measurement noise: member centroids
            # are computed from float32 vertex data, so roughly a micrometre is
            # the finest distinction that means anything. Recorded so an epsilon
            # can be checked against it rather than chosen freely.
            for metric in ("pattern.spacing_max_error", "pattern.spacing_mean_error",
                           "pattern.dimension_spread"):
                found[f"{kind}/{metric}"] = 1e-6
            for metric in ("pattern.spacing_cv", "pattern.dimension_cv"):
                found[f"{kind}/{metric}"] = 1e-6
            found[f"{kind}/pattern.max_angular_deviation"] = 1e-4
        if kind == "coverage":
            samples = certificate["pins"]["views"].get("candidate_count", 1)
            found["coverage/coverage.observed_fraction"] = 1.0 / max(1, samples * 32)
    return found
