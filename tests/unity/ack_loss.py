"""What survives an acknowledgement that never arrives, against a real Unity editor.

Driven by `RoboVisionAckLossGate`, which starts the host, arms the transport's
fault seams on request and pumps the socket while this process talks to it. The
driver here is the public `HostSession` — the same object the MCP adapter holds,
unchanged — because the claim being tested is that the public client is
host-agnostic. A Unity-shaped copy of it would prove nothing about that.

Seven windows, seven different questions:

- **begin, applied** — does the credential still exist, and can it still be
  associated with the transaction the host actually opened?
- **begin, never applied** — is the client honest that there is nothing to
  reclaim, rather than inventing a transaction to hold a credential for?
- **adopt, applied** — the host rotated; does the client know its replacement is
  the current one?
- **adopt, never applied** — the host never rotated; does the client know its
  original is still current?
- **commit** — the work finished; is the outcome read rather than re-applied,
  with the evidence that made it meaningful?
- **discard** — the same, for an outcome whose proof is a reason rather than a
  fingerprint.
- **mutation** — the scene changed and nobody heard; does the same key replay
  instead of authoring a second object?

Unity reaches these windows more often than Blender does, because a domain
reload destroys the bridge at a moment nobody chose. That case is proved in the
EditMode suite; this one is about the socket.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2])
REPORT = Path(sys.argv[3])

sys.path.insert(0, str(REPO))
os.environ["ROBOVISION_UNITY_PORT"] = str(PORT)

from robovision import mcp_server  # noqa: E402
from robovision.errors import RoboVisionError  # noqa: E402

FINDINGS: dict[str, object] = {}
FAILURES: list[str] = []


def expect(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def session():
    return mcp_server._session("unity")


def lost(fn) -> str:
    """Run something whose reply is about to vanish, and name what came back."""
    try:
        fn()
    except RoboVisionError as exc:
        return exc.payload.code
    return "SUCCEEDED"


def arm(method: str, when: str = "after") -> None:
    """Lose exactly one delivery of `method`, at the named side of the handler."""
    session().call("test.fault", {"method": method, "when": when})


def disarm() -> None:
    session().call("test.fault", {})


def wait_for_orphan(timeout_calls: int = 400) -> dict:
    """Wait for the host to notice a socket it was holding has gone.

    Not a paper-over: the disconnect is genuinely asynchronous. The host learns
    of a dead client when it next polls, and Unity services the newest
    connection first, so the reconnected session can be answered before the
    corpse is swept. Polling for the real event is honest; sleeping a fixed
    interval and hoping would not be.
    """
    for _ in range(timeout_calls):
        state = (session().call("system.hello")["result"]["transaction"] or {}).get("state")
        if state and state.get("state") == "orphaned":
            return state
    raise AssertionError("the host never noticed the owner's connection had gone")


def objects() -> list[str]:
    described = session().call("scene.describe")["result"]
    return sorted(
        obj["name"]
        for scene in described["scenes"]
        for obj in scene["objects"]
    )


def create(name: str, **envelope) -> dict:
    return session().call("object.create", {"name": name}, **envelope)


# --------------------------------------------------------------------- begin


def a_lost_begin_keeps_the_credential_and_finds_its_transaction() -> None:
    """The window a host-minted credential loses outright."""
    session().call("scene.snapshot")
    pending_before = session().pending_begins()

    arm("transaction.begin")
    FINDINGS["begin_error"] = lost(lambda: session().begin_transaction("lost ack"))
    disarm()
    FINDINGS["begin_pending_credential"] = session().pending_begins() - pending_before

    # The host applied it: a transaction exists, and the dead socket orphaned it.
    wait_for_orphan()
    recoverable = session().recoverable_transaction()
    FINDINGS["begin_orphan_found"] = recoverable is not None
    FINDINGS["begin_orphan_state"] = (recoverable or {}).get("state")
    transaction = (recoverable or {}).get("transaction")
    FINDINGS["begin_credential_bound"] = session().holds_credential_for(transaction or "")
    FINDINGS["begin_pending_after_bind"] = session().pending_begins() - pending_before

    adopted = session().adopt_transaction(transaction)
    FINDINGS["begin_adopt_ok"] = adopted.get("ok")
    session().end_transaction("transaction.discard", transaction)


def a_begin_that_never_arrived_leaves_nothing_to_reclaim() -> None:
    """Killed in flight: the host never opened anything, and nothing pretends it did."""
    session().call("scene.snapshot")
    pending_before = session().pending_begins()

    arm("transaction.begin", when="before")
    FINDINGS["unbegun_error"] = lost(lambda: session().begin_transaction("never arrived"))
    disarm()

    # The credential is still held — it protects a side effect that may or may
    # not have happened, and this is the case where it did not.
    FINDINGS["unbegun_pending"] = session().pending_begins() - pending_before
    FINDINGS["unbegun_recoverable"] = session().recoverable_transaction()
    FINDINGS["unbegun_host_transaction"] = (
        session().call("system.hello")["result"]["transaction"]["active"])


# --------------------------------------------------------------------- adopt


def orphan(label: str) -> str:
    """Open a transaction and lose the connection that owns it."""
    transaction = session().begin_transaction(label)["result"]["transaction"]
    create(f"Inside{label.title().replace(' ', '')}")
    session().close()
    wait_for_orphan()
    session().recoverable_transaction()
    return transaction


def a_lost_adoption_that_applied_promotes_the_replacement() -> None:
    """The host rotated; the client must know its next secret is now current."""
    transaction = orphan("rotated")

    arm("transaction.adopt")
    FINDINGS["rotate_error"] = lost(lambda: session().adopt_transaction(transaction))
    disarm()

    # The adoption applied, so the host briefly had an owner again and lost it
    # with the same socket. Wait for that second orphaning rather than racing it.
    wait_for_orphan()
    state = session().recoverable_transaction()
    FINDINGS["rotate_generation"] = state["credential_generation"]
    FINDINGS["rotate_reconciled"] = state["credential_reconciled"]
    # The credential it now holds must be the one the host expects.
    again = session().adopt_transaction(transaction)
    FINDINGS["rotate_readopt_ok"] = again.get("ok")
    session().end_transaction("transaction.discard", transaction)


def a_lost_adoption_that_never_applied_keeps_the_original() -> None:
    """The request died in flight, so nothing rotated and the old secret still works."""
    transaction = orphan("unrotated")

    arm("transaction.adopt", when="before")
    FINDINGS["unrotated_error"] = lost(lambda: session().adopt_transaction(transaction))
    disarm()

    state = session().recoverable_transaction()
    FINDINGS["unrotated_generation"] = state["credential_generation"]
    FINDINGS["unrotated_reconciled"] = state["credential_reconciled"]
    again = session().adopt_transaction(transaction)
    FINDINGS["unrotated_readopt_ok"] = again.get("ok")
    session().end_transaction("transaction.discard", transaction)


# ------------------------------------------------------------------ terminal


def public_status(transaction: str) -> dict:
    """What an agent calls: the MCP transaction status tool, by its own path."""
    return session().terminal_state(transaction)


def a_lost_terminal_reply_is_read_not_repeated(method: str, prefix: str, outcome: str) -> None:
    """The work finished; the outcome is retrieved rather than re-applied."""
    session().call("scene.snapshot")
    transaction = session().begin_transaction(prefix)["result"]["transaction"]
    create(prefix.title())
    before = objects()

    arm(method)
    FINDINGS[f"{prefix}_error"] = lost(
        lambda: session().end_transaction(method, transaction))
    disarm()

    resolved = public_status(transaction)
    FINDINGS[f"{prefix}_outcome"] = resolved["outcome"]
    FINDINGS[f"{prefix}_evidence"] = sorted(resolved["finished"] or {})
    FINDINGS[f"{prefix}_no_repeat"] = objects() == before
    FINDINGS[f"{prefix}_credential_dropped"] = not session().holds_credential_for(transaction)
    FINDINGS[f"{prefix}_expected_outcome"] = outcome


# ------------------------------------------------------------------ mutation


def a_lost_mutation_reply_is_replayed_not_reapplied() -> None:
    """The scene changed and nobody heard. The same key must not author twice."""
    session().call("scene.snapshot")
    before = objects()

    arm("object.create")
    FINDINGS["mutation_error"] = lost(
        lambda: create("Unheard", idempotency_key="ack-k1", attempt=1))
    disarm()

    applied = objects()
    FINDINGS["mutation_applied"] = applied != before

    replay = create("Unheard", idempotency_key="ack-k1", attempt=2)
    FINDINGS["mutation_replayed"] = replay.get("replayed")
    FINDINGS["mutation_no_repeat"] = objects() == applied
    FINDINGS["mutation_original_world"] = (
        replay.get("original_execution", {}).get("world_incarnation")
        == session().call("scene.describe")["result"]["world_incarnation"])


# ----------------------------------------------------------------- assertions


def check() -> None:
    expect(FINDINGS["begin_error"] == "SESSION_LOST",
           f"a dropped begin reply was not surfaced: {FINDINGS['begin_error']}")
    expect(FINDINGS["begin_pending_credential"] == 1,
           "the credential for a lost begin was not held before the request went out")
    expect(FINDINGS["begin_orphan_found"], "the transaction the host opened could not be found")
    expect(FINDINGS["begin_orphan_state"] == "orphaned",
           f"the interrupted transaction was not orphaned: {FINDINGS['begin_orphan_state']}")
    expect(FINDINGS["begin_credential_bound"],
           "the credential never found the transaction it belonged to")
    expect(FINDINGS["begin_pending_after_bind"] == 0,
           "a bound credential was still counted as pending")
    expect(FINDINGS["begin_adopt_ok"], "the reclaimed transaction could not be adopted")

    expect(FINDINGS["unbegun_error"] == "SESSION_LOST",
           f"a begin that never arrived was not surfaced: {FINDINGS['unbegun_error']}")
    expect(FINDINGS["unbegun_pending"] == 1,
           "the credential for an unapplied begin was discarded")
    expect(FINDINGS["unbegun_recoverable"] is None,
           f"a transaction was invented for a begin that never arrived: "
           f"{FINDINGS['unbegun_recoverable']}")
    expect(FINDINGS["unbegun_host_transaction"] is False,
           "the host opened a transaction from a request it never received")

    expect(FINDINGS["rotate_error"] == "SESSION_LOST",
           f"a dropped adoption reply was not surfaced: {FINDINGS['rotate_error']}")
    expect(FINDINGS["rotate_generation"] == 1,
           f"an applied rotation was not promoted: {FINDINGS['rotate_generation']}")
    expect(FINDINGS["rotate_reconciled"], "the credential was left ambiguous after reconciling")
    expect(FINDINGS["rotate_readopt_ok"], "the promoted credential did not prove ownership")

    expect(FINDINGS["unrotated_error"] == "SESSION_LOST",
           f"an adoption that never arrived was not surfaced: {FINDINGS['unrotated_error']}")
    expect(FINDINGS["unrotated_generation"] == 0,
           f"a rotation that never happened was promoted: {FINDINGS['unrotated_generation']}")
    expect(FINDINGS["unrotated_reconciled"], "the credential was left ambiguous after reconciling")
    expect(FINDINGS["unrotated_readopt_ok"], "the original credential stopped working")

    for prefix, proofs in (("discarded", ("reason",)),
                           ("committed", ("begin_fingerprint", "final_fingerprint"))):
        expect(FINDINGS[f"{prefix}_error"] == "SESSION_LOST",
               f"a dropped {prefix} reply was not surfaced: {FINDINGS[f'{prefix}_error']}")
        expect(FINDINGS[f"{prefix}_outcome"] == FINDINGS[f"{prefix}_expected_outcome"],
               f"the {prefix} outcome was not resolved: {FINDINGS[f'{prefix}_outcome']}")
        for proof in proofs:
            expect(proof in FINDINGS[f"{prefix}_evidence"],
                   f"the {prefix} record carried no {proof}: {FINDINGS[f'{prefix}_evidence']}")
        expect(FINDINGS[f"{prefix}_no_repeat"],
               f"resolving the {prefix} outcome repeated the side effect")
        expect(FINDINGS[f"{prefix}_credential_dropped"],
               f"a resolved {prefix} transaction kept its credential")

    expect(FINDINGS["mutation_error"] == "SESSION_LOST",
           f"a dropped mutation reply was not surfaced: {FINDINGS['mutation_error']}")
    expect(FINDINGS["mutation_applied"],
           "the fault seam dropped the reply without the host applying the mutation")
    expect(FINDINGS["mutation_replayed"] is True,
           "a redelivered mutation was executed rather than replayed")
    expect(FINDINGS["mutation_no_repeat"], "a redelivered mutation authored a second object")
    expect(FINDINGS["mutation_original_world"],
           "the replay did not report the world the original executed in")


def main() -> int:
    try:
        a_lost_begin_keeps_the_credential_and_finds_its_transaction()
        a_begin_that_never_arrived_leaves_nothing_to_reclaim()
        a_lost_adoption_that_applied_promotes_the_replacement()
        a_lost_adoption_that_never_applied_keeps_the_original()
        a_lost_terminal_reply_is_read_not_repeated("transaction.discard", "discarded", "abandoned")
        a_lost_terminal_reply_is_read_not_repeated("transaction.commit", "committed", "committed")
        a_lost_mutation_reply_is_replayed_not_reapplied()
        check()
    except Exception as exc:  # noqa: BLE001 - the report is the whole output
        import traceback
        FAILURES.append(f"{type(exc).__name__}: {exc}")
        FINDINGS["traceback"] = traceback.format_exc()
    finally:
        try:
            session().close()
        except Exception:  # noqa: BLE001
            pass

    report = {"ok": not FAILURES, "failures": FAILURES, "findings": FINDINGS}
    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
