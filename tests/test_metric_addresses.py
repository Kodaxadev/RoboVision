"""v3b changes one thing: the packet says what each metric is called.

These pin that it changes exactly that. Every address shown must be one the
evaluator resolves to the very number displayed beside it — otherwise v3b would be
handing participants a new way to be wrong. Nothing already in the packet may move,
and nothing may be addressed that is not a certificate metric. v2 and v3a packets
must stay as they were built.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from benchmarks.addresses import addresses_enabled, annotate_addresses
from benchmarks.report import vector

ROOT = Path(__file__).resolve().parents[1]


def metric(value, direction="lower_better"):
    return {"value": value, "direction": direction}


BUNDLE = {
    "ref:front": {"metrics": {
        "reference.macro.silhouette_iou": metric(0.862899, "higher_better"),
        "reference.macro.excess_fraction": metric(0.139025),
        "reference.macro.deficit_fraction": metric(0.017136),
        "reference.macro.aspect_error": metric(0.05),
        "reference.contour.mean_distance": metric(0.003347),
        "reference.contour.worst_sector_net": metric(0.733333),
    }},
    "pattern:brackets": {"metrics": {
        "pattern.spacing_max_error": metric(4.900687208),
        "pattern.max_angular_deviation": metric(0.019782),
        "pattern.dimension_cv": metric(0.0),
    }},
    "spatial": {"metrics": {
        "spatial.dimension_x": metric(0.567236722, "neutral"),
        "spatial.dimension_y": metric(0.485132694, "neutral"),
        "spatial.dimension_z": metric(0.830865026, "neutral"),
        "spatial.largest_dimension": metric(0.630395353, "neutral"),
    }},
    "coverage": {"metrics": {"coverage.observed_fraction": metric(0.915716, "higher_better")}},
    "geometry": {"metrics": {"geometry.faces": metric(54, "neutral")}},
}


def evidence():
    return {
        "hard_invariants": {"status": "pass", "failing": [], "openness": {"Mast": "closed"}},
        "dimensions": {"size": [0.567236722, 0.485132694, 0.830865026],
                       "largest": 0.630395353, "failing": [], "unit": "metre"},
        "coverage": {"observed_fraction": 0.915716,
                     "largest_unverified_region": {"area": 0.0957, "centroid": [0, 0, 0]}},
        "reference": {"front": {
            "silhouette_iou": 0.862899, "excess_fraction": 0.139025,
            "deficit_fraction": 0.017136, "aspect_error": 0.05,
            "contour_mean_distance": 0.003347, "worst_sector": "right",
            "worst_sector_sign": "excess", "worst_sector_magnitude": 0.733333,
            "sectors": [{"sector": "right", "net_fraction": 0.26}],
            "evidence": "projected geometric occupancy"}},
        "patterns": {"brackets": {
            "expected_count": 5, "actual_count": 5, "max_spacing_error": 4.900687208,
            "worst_gap_index": 0, "max_angular_deviation": 0.019782,
            "dimension_cv": 0.0, "failing": ["pattern.spacing_within_tolerance"]}},
    }


def all_addresses(annotated):
    for name in ("reference", "patterns"):
        for entry in annotated[name].values():
            yield entry, entry["addresses"]
    for name in ("dimensions", "coverage"):
        yield annotated[name], annotated[name]["addresses"]


def shown(section, field):
    if field.startswith("size["):
        return section["size"][int(field[5:-1])]
    return section[field]


# ------------------------------------------------------------ copyability

def test_every_address_resolves_to_the_number_shown_beside_it():
    """The evaluator's lookup, applied to each address, finds the displayed value."""
    annotated = annotate_addresses(evidence(), BUNDLE)
    count = 0
    for section, addresses in all_addresses(annotated):
        for field, address in addresses.items():
            resolved = BUNDLE[address["kind"]]["metrics"][address["metric"]]
            assert resolved["value"] == shown(section, field)
            count += 1
    # 6 reference fields, 3 pattern fields, 4 dimension fields, 1 coverage field.
    assert count == 14


def test_the_names_the_v3a_participants_needed_are_exactly_what_is_shown():
    annotated = annotate_addresses(evidence(), BUNDLE)
    assert annotated["reference"]["front"]["addresses"]["excess_fraction"] == {
        "metric": "reference.macro.excess_fraction", "kind": "ref:front"}
    # The one Nemotron never tried: the same words in the other order.
    assert annotated["patterns"]["brackets"]["addresses"]["max_spacing_error"] == {
        "metric": "pattern.spacing_max_error", "kind": "pattern:brackets"}


def test_an_address_is_only_metric_and_kind():
    """No direction, no resolution: those would be a second change."""
    for _section, addresses in all_addresses(annotate_addresses(evidence(), BUNDLE)):
        for address in addresses.values():
            assert set(address) == {"metric", "kind"}


def test_a_mapping_that_would_point_at_a_different_number_is_omitted():
    bundle = copy.deepcopy(BUNDLE)
    bundle["ref:front"]["metrics"]["reference.macro.excess_fraction"]["value"] = 0.5
    annotated = annotate_addresses(evidence(), bundle)
    assert "excess_fraction" not in annotated["reference"]["front"]["addresses"]


def test_a_metric_the_evaluator_cannot_resolve_is_never_addressed():
    bundle = copy.deepcopy(BUNDLE)
    del bundle["pattern:brackets"]
    annotated = annotate_addresses(evidence(), bundle)
    assert annotated["patterns"]["brackets"]["addresses"] == {}


# -------------------------------------------------------------- additive only

def test_nothing_already_in_the_packet_moves():
    original = evidence()
    annotated = annotate_addresses(copy.deepcopy(original), BUNDLE)
    for _section, _ in all_addresses(annotated):
        pass
    stripped = copy.deepcopy(annotated)
    for name in ("reference", "patterns"):
        for entry in stripped[name].values():
            entry.pop("addresses")
    for name in ("dimensions", "coverage"):
        stripped[name].pop("addresses")
    assert stripped == original


def test_descriptive_detail_is_not_addressed():
    annotated = annotate_addresses(evidence(), BUNDLE)
    front = annotated["reference"]["front"]["addresses"]
    brackets = annotated["patterns"]["brackets"]["addresses"]
    for field in ("worst_sector", "worst_sector_sign", "sectors", "evidence"):
        assert field not in front
    for field in ("expected_count", "actual_count", "worst_gap_index", "failing"):
        assert field not in brackets
    assert "largest_unverified_region" not in annotated["coverage"]["addresses"]


def test_invariant_presentation_is_untouched():
    annotated = annotate_addresses(evidence(), BUNDLE)
    assert annotated["hard_invariants"] == evidence()["hard_invariants"]


def test_the_comparison_vector_is_unchanged_so_q0_verification_is_unaffected():
    """Restore checks Q0 through `vector`; addresses must be invisible to it."""
    assert vector(annotate_addresses(evidence(), BUNDLE)) == vector(evidence())


# ------------------------------------------------------------------ gating

def test_v2_and_v3a_do_not_annotate():
    for package in ("correction-transfer-v2", "correction-transfer-v3-feedback"):
        manifest = json.loads((ROOT / "benchmarks" / package / "MANIFEST.json")
                              .read_text(encoding="utf-8"))
        assert addresses_enabled(manifest) is False, package


def test_v3b_annotates_and_keeps_failure_feedback():
    manifest = json.loads((ROOT / "benchmarks" / "correction-transfer-v3b-legibility"
                           / "MANIFEST.json").read_text(encoding="utf-8"))
    assert addresses_enabled(manifest) is True
    assert manifest["harness"]["return_failure_causes"] is True
