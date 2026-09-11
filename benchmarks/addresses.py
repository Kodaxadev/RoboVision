"""v3b: every metric the discrepancy packet shows carries the address a correction must name.

The packet displays friendly fields — `excess_fraction`, `max_spacing_error` — while
the evaluator only accepts canonical certificate identifiers —
`reference.macro.excess_fraction`, `pattern.spacing_max_error`. In v3a one clean
participant spent six attempts guessing that translation and never found it; the
pattern name is the same words in the other order, and nothing in the packet said
so. Another participant found the names by calling the truth tools directly. v3b
asks whether removing the translation lets the first kind of model do what the
second already could.

So this adds, beside the fields each section already has, an `addresses` map from
friendly field to the exact `{metric, kind}` pair a correction's `targets` or
`protected` entry takes. It is deliberately the whole change:

- **additive only** — no existing value, key or type is altered;
- **only `{metric, kind}`** — no direction, no resolution, no guidance prose;
- **invariants untouched** — separating invariants from metrics is a later ablation;
- **provably copyable** — an address is emitted only if the evaluator's own lookup,
  `certificates[kind]["metrics"][metric]`, resolves and holds exactly the value the
  packet displays. A mapping that ever pointed at a different number is omitted
  rather than shown.

Descriptive detail is not addressed: sector breakdowns, region geometry, counts
read from invariant evidence, gap indices and labels. Those are not certificate
metrics, and an address that named something the evaluator cannot resolve would be
worse than none.
"""
from __future__ import annotations

from typing import Any

# Per-entry sections: one entry per reference view or declared pattern. The kind
# is the bundle key the evaluator resolves against.
ENTRY_FIELDS = {
    "reference": ("ref:", {
        "silhouette_iou": "reference.macro.silhouette_iou",
        "excess_fraction": "reference.macro.excess_fraction",
        "deficit_fraction": "reference.macro.deficit_fraction",
        "aspect_error": "reference.macro.aspect_error",
        "contour_mean_distance": "reference.contour.mean_distance",
        "worst_sector_magnitude": "reference.contour.worst_sector_net",
    }),
    "patterns": ("pattern:", {
        "max_spacing_error": "pattern.spacing_max_error",
        "max_angular_deviation": "pattern.max_angular_deviation",
        "dimension_cv": "pattern.dimension_cv",
    }),
}

# Single sections, each resolved against one certificate. `size[i]` addresses one
# element of the displayed dimensions list.
SECTION_FIELDS = {
    "dimensions": ("spatial", {
        "size[0]": "spatial.dimension_x",
        "size[1]": "spatial.dimension_y",
        "size[2]": "spatial.dimension_z",
        "largest": "spatial.largest_dimension",
    }),
    "coverage": ("coverage", {
        "observed_fraction": "coverage.observed_fraction",
    }),
}


def addresses_enabled(manifest: dict[str, Any]) -> bool:
    """Gated on the benchmark's own manifest, so v2 and v3a stay as built."""
    return bool((manifest.get("harness") or {}).get("metric_addresses"))


def _displayed(section: dict[str, Any], field: str) -> Any:
    if field.startswith("size[") and field.endswith("]"):
        values = section.get("size") or []
        index = int(field[5:-1])
        return values[index] if index < len(values) else None
    return section.get(field)


def _address(bundle: dict[str, Any], kind: str, metric: str, shown: Any) -> dict | None:
    """The pair, if and only if the evaluator would resolve it to the shown number."""
    if isinstance(shown, bool) or not isinstance(shown, (int, float)):
        return None
    entry = (bundle.get(kind) or {}).get("metrics", {}).get(metric)
    if not isinstance(entry, dict) or entry.get("value") != shown:
        return None
    return {"metric": metric, "kind": kind}


def _addresses(bundle: dict[str, Any], section: dict[str, Any], kind: str,
               fields: dict[str, str]) -> dict[str, dict]:
    found = {}
    for field, metric in fields.items():
        address = _address(bundle, kind, metric, _displayed(section, field))
        if address is not None:
            found[field] = address
    return found


def annotate_addresses(evidence: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    """Add an `addresses` map beside each section's fields. Nothing else changes."""
    for section_name, (prefix, fields) in ENTRY_FIELDS.items():
        for key, entry in (evidence.get(section_name) or {}).items():
            if isinstance(entry, dict):
                entry["addresses"] = _addresses(bundle, entry, prefix + key, fields)
    for section_name, (kind, fields) in SECTION_FIELDS.items():
        section = evidence.get(section_name)
        if isinstance(section, dict):
            section["addresses"] = _addresses(bundle, section, kind, fields)
    return evidence
