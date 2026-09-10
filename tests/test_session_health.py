"""What the session adds to a health report, and what it refuses to add.

The host cannot know whether anyone still holds the credential that would
reclaim a transaction — it holds a verifier, not a secret, and it has no idea
which process is on the other end of a socket. The client library does know, and
is the only thing that may say so.

Exercised without a host, because what is under test is the boundary between the
two truths rather than either of them. A fake connection makes the host's answer
a fixture, which is exactly what it needs to be here.
"""
from __future__ import annotations

from typing import Any

import pytest

from robovision.session import HostSession, _Credential


class FakeClient:
    """Records what was sent and answers with a fixed report."""

    def __init__(self, report: dict[str, Any]):
        self.report = report
        self.calls: list[str] = []

    def call(self, method: str, params: dict[str, Any], **fields: Any) -> dict[str, Any]:
        self.calls.append(method)
        return {"ok": True, "revision": 3, "result": self.report}

    def close(self) -> None:
        pass


def report_for(transaction: str | None, *, adoption_required: bool) -> dict[str, Any]:
    state = None if transaction is None else {
        "transaction": transaction,
        "state": "orphaned" if adoption_required else "active",
        "adoption_required": adoption_required,
    }
    return {
        "status": "ready",
        "identity": {"world_incarnation": "rvworld:x", "coordinate_contract": "rvcoord:x"},
        "ready_for": {"semantic_observation": {"status": "ready"}},
        "subsystems": {"transaction": {"situation": "none", "state": state}},
    }


def session_with(report: dict[str, Any]) -> tuple[HostSession, FakeClient]:
    session = HostSession("127.0.0.1", 9999)
    client = FakeClient(report)
    session._client = client
    session._generation = 1
    return session, client


def test_health_reuses_the_sessions_own_connection():
    """Ownership *is* the connection, so a second socket would describe nobody.

    A health report fetched over a fresh connection would answer about a caller
    that ceases to exist as the call returns — and would orphan whatever the
    original connection was holding on its way out.
    """
    session, client = session_with(report_for(None, adoption_required=False))
    session.health()
    session.health()
    assert client.calls == ["system.health", "system.health"]
    assert session.generation == 1, "health opened a new connection"


def test_host_truth_and_session_truth_are_reported_separately():
    session, _ = session_with(report_for(None, adoption_required=False))
    response = session.health()
    assert "session" in response and "result" in response
    assert "recoverable_by_this_session" not in response["result"], (
        "a session-only fact was folded into the host's own report")


def test_a_session_that_holds_the_credential_for_an_orphan_says_so():
    session, _ = session_with(report_for("rvtx:t", adoption_required=True))
    session._credentials["rvtx:t"] = _Credential(generation=0, secret="s")
    view = session.health()["session"]
    assert view["holds_recovery_credential"] is True
    assert view["recoverable_by_this_session"] is True


def test_a_session_that_never_opened_the_transaction_claims_nothing():
    session, _ = session_with(report_for("rvtx:someone-elses", adoption_required=True))
    view = session.health()["session"]
    assert view["holds_recovery_credential"] is False
    assert view["recoverable_by_this_session"] is False


def test_an_active_transaction_this_session_owns_is_not_recoverable():
    """Recoverable means "adoption would work", not "I have a secret somewhere".

    An active transaction needs no reclaiming, and reporting one as recoverable
    would invite a call that adoption exists to refuse.
    """
    session, _ = session_with(report_for("rvtx:t", adoption_required=False))
    session._credentials["rvtx:t"] = _Credential(generation=0, secret="s")
    view = session.health()["session"]
    assert view["holds_recovery_credential"] is True
    assert view["recoverable_by_this_session"] is False


def test_the_credential_itself_never_appears():
    session, _ = session_with(report_for("rvtx:t", adoption_required=True))
    session._credentials["rvtx:t"] = _Credential(generation=0, secret="the-actual-secret")
    assert "the-actual-secret" not in str(session.health())


@pytest.mark.parametrize("report", [
    {},
    {"subsystems": {}},
    {"subsystems": {"transaction": {}}},
    {"subsystems": {"transaction": {"state": None}}},
])
def test_a_report_without_a_transaction_block_is_not_an_error(report):
    """A host that reports less than expected is not a reason to fail the caller.

    The session's contribution is an enrichment. If it cannot find a transaction
    to speak about, the honest answer is that it holds nothing for one — not an
    exception raised on the path an agent uses to ask whether things are working.
    """
    session, _ = session_with(report)
    view = session.health()["session"]
    assert view["recoverable_by_this_session"] is False
