"""What a run returns to its participant, and what it must find before acting.

Two pure functions, kept apart from the runner because each is a contract the
benchmark's validity rests on and each is tested without an editor:

- `participant_result` decides what `submit_correction` hands back. v2 returned
  success evidence only; v3a returns the evaluator's failure causes too, gated on
  each benchmark's own manifest so v2 stays reproducible as built.
- `expected_state` decides what scene a run must find before it acts again, which
  is how a run refuses to act on a scene another run has since used.
"""
from __future__ import annotations


# The evaluator's own fields, exposed as computed. Nothing is paraphrased or
# summarised: the point of v3a is to change what the participant is told, and a
# harness that rewrote the evaluator's findings into friendlier prose would be
# changing a second thing at the same time.
FAILURE_FIELDS = ("reject_causes", "indeterminate_causes",
                  "targets_achieved", "invariants_checked")


def participant_result(record: dict, *, n: int, budget: int, evidence: dict,
                       return_causes: bool) -> dict:
    """What `submit_correction` hands back to the participant.

    v2 returned the decision, the targets achieved and the epsilon audit, and
    never the causes of a rejection. `CHALLENGE.md` had promised "you try again
    with the evidence from the failure", so the loop was missing its negative
    feedback channel: a participant could see what had worked in a rejected
    candidate but not why the candidate was refused. Both clean v2 participants
    were shaped by that.

    `return_causes` adds the evaluator's record under `evaluation`, **including
    indeterminate causes when a reject cause outranked them for the decision** —
    a participant that listed an invariant as a protected metric must learn so
    even in an attempt that was rejected for something else.

    The v2 shape is otherwise untouched, and v2 keeps it: the flag comes from the
    benchmark's own manifest, so a v2 run stays reproducible exactly as built.
    """
    evaluation = record["evaluation"]
    result = {
        "attempt": record["attempt"],
        "n": n, "budget": budget,
        "decision": evaluation["decision"],
        "outcome": record["outcome"],
        "restored": record["restored"],
        "targets_achieved": evaluation["targets_achieved"],
        "epsilon_audit": record["epsilon_audit"],
        "evidence": evidence,
    }
    if return_causes:
        result["evaluation"] = {field: evaluation.get(field, [])
                                for field in FAILURE_FIELDS}
    return result


# Files whose presence means a run identity has already been used for an
# experiment. Restore artifacts alone (brief, packet-q0, verification) do not
# count: re-restoring a run that no participant has touched is harmless.
LIFECYCLE_FILES = ("trajectory.json", "stop.json", "report.json")


def expected_state(attempts: list, frozen_signature: str | None) -> dict:
    """The scene state a run must find in the editor before it acts again.

    Before any attempt, the restored A0 — compared by normalized signature,
    because a fresh restore differs from every other in its UUIDs. After an
    attempt, the exact authored fingerprint that attempt left behind: its final
    fingerprint when one was taken, or its begin fingerprint when it was
    verifiably rolled back. An attempt whose closing evidence was lost has no
    trustworthy end state, and that is reported rather than guessed.
    """
    if not attempts:
        if frozen_signature is None:
            return {"kind": "unknown",
                    "reason": "no attempts yet and no frozen signature published"}
        return {"kind": "signature", "value": frozen_signature}
    last = attempts[-1]
    if last.get("final_fingerprint"):
        return {"kind": "fingerprint", "value": last["final_fingerprint"]}
    if last.get("restored") is True and last.get("begin_fingerprint"):
        return {"kind": "fingerprint", "value": last["begin_fingerprint"]}
    return {"kind": "unknown",
            "reason": f"attempt {last.get('attempt')} left no trustworthy end "
                      f"state (phase {last.get('phase')})"}
