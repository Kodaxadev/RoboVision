"""v3a changes one thing: the participant is told why an attempt failed.

These pin that the change is exactly that and no larger. v2 must keep returning
what it returned — its runs are frozen evidence and have to stay reproducible as
built — and v3a must add the evaluator's own record without paraphrasing it.

The case that matters most is Participant 2's attempt 1 in v2: a reject cause
decided the outcome while an indeterminate cause — `geometry.manifold` listed as
a protected metric — sat underneath it, in all eight attempts, and was never
seen. v3a must return both.
"""
from __future__ import annotations

from benchmarks.runner import FAILURE_FIELDS, participant_result

RECORD = {
    "attempt": "attempt:abc",
    "outcome": "rolled_back",
    "restored": True,
    "epsilon_audit": [{"metric": "pattern.spacing_max_error", "epsilon": 1.0}],
    "evaluation": {
        "schema": 1,
        "decision": "reject",
        "accepted": False,
        "reject_causes": [{"cause": "insufficient_target_improvement",
                           "metric": "reference.macro.excess_fraction",
                           "kind": "ref:front", "required_improvement": 0.03,
                           "achieved_improvement": 0.014751}],
        "indeterminate_causes": [{"cause": "protected_metric_missing",
                                  "metric": "geometry.manifold", "kind": "geometry"}],
        "targets_achieved": [{"metric": "pattern.spacing_max_error",
                              "kind": "pattern:brackets"}],
        "invariants_checked": ["geometry.manifold"],
        "policy": {"targets": []},
        "note": "internal",
    },
}


def result(return_causes):
    return participant_result(RECORD, n=1, budget=8, evidence={"packet": True},
                              return_causes=return_causes)


def test_v2_returns_exactly_what_it_returned():
    """Frozen evidence stays reproducible: no new key appears for v2."""
    assert set(result(False)) == {"attempt", "n", "budget", "decision", "outcome",
                                  "restored", "targets_achieved", "epsilon_audit",
                                  "evidence"}


def test_v3a_adds_the_evaluation_block_and_nothing_else():
    assert set(result(True)) - set(result(False)) == {"evaluation"}
    # Everything v2 returned is still returned, unchanged.
    for key, value in result(False).items():
        assert result(True)[key] == value


def test_v3a_returns_the_evaluators_fields_as_computed():
    evaluation = result(True)["evaluation"]
    assert set(evaluation) == set(FAILURE_FIELDS)
    for field in FAILURE_FIELDS:
        assert evaluation[field] == RECORD["evaluation"][field]


def test_an_indeterminate_cause_is_returned_under_a_reject_decision():
    """The exact v2 blind spot: rejected for one thing, silently wrong on another."""
    returned = result(True)
    assert returned["decision"] == "reject"
    assert returned["evaluation"]["indeterminate_causes"] == [
        {"cause": "protected_metric_missing", "metric": "geometry.manifold",
         "kind": "geometry"}]


def test_the_evaluators_internals_are_not_exposed():
    """The policy echo and note are the evaluator's, not feedback. Not a second change."""
    evaluation = result(True)["evaluation"]
    assert "policy" not in evaluation and "note" not in evaluation


def test_a_record_without_causes_yields_empty_lists_not_a_crash():
    record = {**RECORD, "evaluation": {"decision": "accept", "targets_achieved": []}}
    returned = participant_result(record, n=1, budget=8, evidence={},
                                  return_causes=True)
    assert returned["evaluation"]["reject_causes"] == []
    assert returned["evaluation"]["indeterminate_causes"] == []
