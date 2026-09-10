"""What an attempt records when it does not finish normally.

Two failures that look alike from the driver's stack trace and are nothing alike
in what they permit anyone to say afterwards.

**Before the transaction is terminal**, an exception means the candidate was
never judged. Fail closed, roll back, and record `indeterminate`. Calling that a
rejection would imply the metrics had spoken.

**After the transaction is terminal**, the candidate's outcome is already a fact.
A commit that succeeded is committed; a rollback that succeeded is rolled back.
If the *evidence collection* then fails — the closing snapshot, the trajectory
write — that is a failure to record what happened, not a change in what
happened. Relabelling a committed mutation as "indeterminate, rolled back" would
be the single most dangerous lie this harness could tell: the scene would carry
the change while the record denied it.

So the terminal outcome is preserved, the evidence failure is reported as its own
thing, and the attempt is marked incomplete for benchmark purposes without any
claim that it was undone.
"""
from __future__ import annotations

import time
from typing import Any

from .errors import RoboVisionError

# Reported instead of a decision. Neither means the candidate was rejected.
INDETERMINATE = "indeterminate"


def _base(attempt_id: str, model: str, started: float, calls: int,
          transaction: str, correction, evidence) -> dict[str, Any]:
    return {
        "attempt": attempt_id,
        "model": model,
        "started_at": round(started, 3),
        "elapsed_seconds": round(time.time() - started, 3),
        "tool_calls": calls,
        "discrepancy_packet": evidence,
        "correction": correction.to_json(),
        "transaction": transaction,
    }


def orchestration_failure(*, attempt_id: str, model: str, started: float,
                          calls: int, transaction: str, snapshot: dict[str, Any],
                          correction, evidence,
                          recovery: dict[str, Any]) -> dict[str, Any]:
    """A driver failure before the transaction reached a terminal state."""
    record = _base(attempt_id, model, started, calls, transaction, correction, evidence)
    record.update({
        "evaluation": {"decision": INDETERMINATE, "accepted": False,
                       "reason": "orchestration_failure", "targets_achieved": []},
        "outcome": "orchestration_failed",
        "phase": "pre_terminal",
        "orchestration_failure": recovery,
        "begin_fingerprint": snapshot["fingerprint"],
        "restored": recovery.get("recovery") in ("rolled_back",
                                                 "adopted_and_rolled_back"),
        "benchmark_validity": "invalid",
    })
    return record


def evidence_failure(session, *, attempt_id: str, model: str, started: float,
                     calls: int, transaction: str, snapshot: dict[str, Any],
                     correction, evidence, terminal: dict[str, Any],
                     cause: BaseException) -> dict[str, Any]:
    """Evidence collection failed *after* the transaction was already terminal.

    The terminal outcome is re-read from the host where that is possible, so the
    record carries the host's own account of what happened rather than the
    driver's memory of what it asked for. Where the read also fails, the outcome
    the driver observed at the boundary stands — it was a successful reply, not
    an assumption — and the record says the confirmation could not be refreshed.
    """
    confirmed: dict[str, Any] | None = None
    read_error: str | None = None
    try:
        confirmed = session.terminal_state(transaction)
    except (RoboVisionError, OSError) as exc:
        read_error = f"{type(exc).__name__}: {exc}"

    record = _base(attempt_id, model, started, calls, transaction, correction, evidence)
    record.update({
        "evaluation": {"decision": terminal["decision"],
                       "accepted": terminal["accepted"],
                       "reason": "evidence_finalization_failed",
                       "targets_achieved": terminal.get("targets_achieved", [])},
        # The candidate's real outcome. Never rewritten by this path.
        "outcome": terminal["outcome"],
        "phase": "post_terminal",
        "finish_proof": terminal.get("proof"),
        "terminal_confirmed": confirmed,
        "evidence_failure": {
            "cause": f"{type(cause).__name__}: {cause}",
            "terminal_state_read_error": read_error,
            "note": "the transaction had already reached a terminal state when "
                    "evidence collection failed; its outcome is reported as it "
                    "happened and no rollback is claimed",
        },
        "begin_fingerprint": snapshot["fingerprint"],
        # Deliberately absent rather than guessed: the closing snapshot is
        # exactly what could not be taken.
        "final_fingerprint": None,
        "restored": None,
        # The attempt is unusable as benchmark evidence — its final vector was
        # never measured — which is a different statement from "it did not
        # happen".
        "benchmark_validity": "incomplete_evidence",
    })
    return record
