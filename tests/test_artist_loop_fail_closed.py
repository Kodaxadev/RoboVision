"""An Artist Loop failure after begin must never leave a transaction open.

The dangerous shape is not an operation that fails — the driver already judged
those — but an exception in the driver's *own* orchestration: Q1 measurement,
the locality read, the evaluator call. v0 would have propagated it with the
transaction still active, and a harness looping over attempts would have opened
the next one on top of it.

The rule these tests pin is narrow and absolute: an incomplete evaluation never
commits. "I could not finish judging this candidate" is not evidence that the
candidate was good, and a driver that resolved that ambiguity in favour of
keeping the work would be the single most expensive bug in the benchmark.
"""
from __future__ import annotations

import pytest

from robovision.artist_loop_delivery import fail_closed
from robovision.errors import RoboVisionError


class Stub:
    """A session that answers exactly the calls the recovery path makes."""

    def __init__(self, *, rollback=None, recoverable=None, terminal=None,
                 credential=True):
        self._rollback = rollback
        self._recoverable = recoverable
        self._terminal = terminal
        self._credential = credential
        self.calls: list[str] = []

    def holds_credential_for(self, transaction):
        return self._credential

    def end_transaction(self, method, transaction, **_fields):
        self.calls.append(method)
        if isinstance(self._rollback, Exception):
            raise self._rollback
        return {"ok": True, "result": self._rollback or {"state": "rolled_back"}}

    def reconnect(self):
        self.calls.append("reconnect")
        return 2

    def recoverable_transaction(self):
        self.calls.append("recoverable_transaction")
        return self._recoverable

    def adopt_transaction(self, transaction, **_fields):
        self.calls.append("adopt")
        return {"ok": True}

    def terminal_state(self, transaction):
        self.calls.append("terminal_state")
        if isinstance(self._terminal, Exception):
            raise self._terminal
        return self._terminal


def lost(code="SESSION_LOST"):
    return RoboVisionError(code, "the connection to the editor host was lost")


def test_plain_rollback_is_the_normal_recovery():
    session = Stub()
    record = fail_closed(session, "tx:1", RuntimeError("evaluator blew up"))

    assert record["recovery"] == "rolled_back"
    assert session.calls == ["transaction.rollback"]
    assert "evaluator blew up" in record["cause"]


def test_a_lost_connection_is_adopted_then_rolled_back():
    """The credential this session already holds is the whole mechanism.

    A driver that reconnected and simply carried on would arrive as a stranger
    holding no authority over the work in progress, and the orphan would sit
    there until something else timed it out.
    """
    session = Stub(rollback=lost(),
                   recoverable={"transaction": "tx:1", "state": "orphaned",
                                "adoption_required": True,
                                "recoverable_by_this_session": True})
    # Only the first rollback fails; after adoption the session is healthy.
    original = session.end_transaction

    def once(method, transaction, **fields):
        try:
            return original(method, transaction, **fields)
        finally:
            session._rollback = None

    session.end_transaction = once
    record = fail_closed(session, "tx:1", RuntimeError("locality read failed"))

    assert record["recovery"] == "adopted_and_rolled_back"
    assert "adopt" in session.calls
    assert session.calls.index("reconnect") < session.calls.index("adopt")


def orphaned():
    return RoboVisionError("TRANSACTION_ORPHANED",
                           "that transaction is waiting to be adopted")


def test_a_refused_rollback_on_an_orphan_adopts_rather_than_walks_away():
    """The window this closes, and why reading was the wrong answer.

    A mutation applies, its reply is lost, the read that follows reconnects the
    session, and the host orphans the transaction the dead socket owned. The
    rollback then comes back TRANSACTION_ORPHANED — a definite answer, not a
    transport loss — so the old code resolved it by reading and reported an
    *active* orphan as `unresolved`. The scene would have kept the model's
    mutation while the record said the attempt was indeterminate.
    """
    session = Stub(rollback=orphaned(),
                   recoverable={"transaction": "tx:1", "state": "orphaned",
                                "adoption_required": True,
                                "recoverable_by_this_session": True})
    original = session.end_transaction

    def once(method, transaction, **fields):
        try:
            return original(method, transaction, **fields)
        finally:
            session._rollback = None

    session.end_transaction = once
    record = fail_closed(session, "tx:1", RuntimeError("Q1 failed"))

    assert record["recovery"] == "adopted_and_rolled_back"
    # No reconnect: the connection was never lost, and reconnecting a healthy
    # session would discard the ownership being reclaimed.
    assert "reconnect" not in session.calls
    assert session.calls.index("adopt") > session.calls.index("recoverable_transaction")


@pytest.mark.parametrize("recoverable,credential,why", [
    ({"transaction": "tx:other", "adoption_required": True}, True,
     "a different transaction is not an invitation to adopt this one"),
    ({"transaction": "tx:1", "adoption_required": False}, True,
     "a transaction that does not require adoption keeps its own outcome"),
    ({"transaction": "tx:1", "adoption_required": True}, False,
     "adoption without the credential is a claim, not a proof"),
    (None, True, "nothing to adopt"),
])
def test_not_every_orphan_refusal_is_adoptable(recoverable, credential, why):
    session = Stub(rollback=orphaned(), recoverable=recoverable,
                   credential=credential,
                   terminal={"transaction": "tx:1", "outcome": None,
                             "finished": None})
    record = fail_closed(session, "tx:1", RuntimeError("Q1 failed"))

    assert "adopt" not in session.calls, why
    assert record["recovery"] == "unresolved"


def test_a_terminal_refusal_is_never_adopted():
    """TRANSACTION_FINISHED keeps its meaning: read it, do not reclaim it."""
    session = Stub(rollback=RoboVisionError("TRANSACTION_FINISHED", "already ended"),
                   recoverable={"transaction": "tx:1", "adoption_required": True},
                   terminal={"transaction": "tx:1", "outcome": "committed",
                             "finished": {"state": "committed"}})
    record = fail_closed(session, "tx:1", RuntimeError("snapshot failed"))

    assert session.calls == ["transaction.rollback", "terminal_state"]
    assert record["recovery"] == "already_committed"


def test_an_unrecoverable_transaction_is_read_not_assumed():
    """No credential, no authority, no claim. The state is asked for."""
    session = Stub(rollback=lost(), recoverable=None,
                   terminal={"transaction": "tx:1", "finished": {"state": "rolled_back"},
                             "outcome": "rolled_back"})
    record = fail_closed(session, "tx:1", RuntimeError("Q1 failed"))

    assert record["recovery"] == "already_rolled_back"
    assert "terminal_state" in session.calls


def test_an_unresolvable_transaction_says_so_rather_than_claiming_safety():
    session = Stub(rollback=lost(), recoverable=None, terminal=lost())
    record = fail_closed(session, "tx:1", RuntimeError("Q1 failed"))

    assert record["recovery"] == "unresolved"
    assert "assumed safe" in record["note"]


@pytest.mark.parametrize("cause", [RuntimeError("boom"), KeyError("revision")])
def test_recovery_never_commits(cause):
    """The one thing that must never appear on this path, under any failure."""
    session = Stub()
    fail_closed(session, "tx:1", cause)
    assert "transaction.commit" not in session.calls


def test_a_refused_rollback_is_resolved_by_reading():
    """A host that answered is asked what it did, not told what it did.

    A commit whose acknowledgement was lost is the case that makes this matter:
    rolling back again would be wrong, and the only honest move is to read the
    terminal state.
    """
    session = Stub(rollback=RoboVisionError("TRANSACTION_FINISHED", "already ended"),
                   terminal={"transaction": "tx:1", "outcome": "committed",
                             "finished": {"state": "committed"}})
    record = fail_closed(session, "tx:1", RuntimeError("snapshot failed"))

    assert record["recovery"] == "already_committed"
    assert session.calls == ["transaction.rollback", "terminal_state"]
