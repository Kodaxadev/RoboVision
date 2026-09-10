"""What survives an acknowledgement that never arrives.

Every recovery guarantee this project makes is about one window: the host has
applied a request and its response has not reached the caller. A test that
reached that state by editing private dictionaries afterwards would be asserting
its own edit, so these drive the real transport and drop the response at exactly
that boundary — after the handler ran, before delivery — then kill the socket.

Four losses, four different questions:

- **begin** — does the credential still exist, and can it still be associated
  with the transaction the host actually opened?
- **adopt, applied** — the host rotated; does the client know its replacement is
  the current one?
- **adopt, not applied** — the host never rotated; does the client know its
  original is still current?
- **terminal** — the work finished; can the outcome be read rather than repeated,
  through the surface an agent actually has?
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from robovision import mcp_server  # noqa: E402
from robovision.errors import RoboVisionError  # noqa: E402
from robovision_blender.runtime import RoboVisionRuntime  # noqa: E402

FINDINGS: dict[str, object] = {}
RUNTIME: RoboVisionRuntime | None = None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def pump(runtime, worker, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        runtime.transport.poll(runtime.dispatch, command_budget=8)
        if time.monotonic() > deadline:
            raise AssertionError("ack loss gate timed out")
        time.sleep(0.001)
    for _ in range(50):
        runtime.transport.poll(runtime.dispatch, command_budget=8)


def drop_response_to(method: str) -> None:
    """Arm the transport to apply the next call to `method` and never answer it."""
    armed = {"used": False}

    def fault(request, response):
        if armed["used"] or request.get("method") != method:
            return True
        armed["used"] = True
        return False

    RUNTIME.transport.fault_after_dispatch = fault


def drop_request_to(method: str) -> None:
    """Arm the transport to lose the next call to `method` before it is applied."""
    armed = {"used": False}

    def fault(request):
        if armed["used"] or request.get("method") != method:
            return True
        armed["used"] = True
        return False

    RUNTIME.transport.fault_before_dispatch = fault


def disarm() -> None:
    RUNTIME.transport.fault_after_dispatch = None
    RUNTIME.transport.fault_before_dispatch = None


def session():
    return mcp_server._session("blender")


def lost(fn) -> str:
    try:
        fn()
    except RoboVisionError as exc:
        return exc.payload.code
    return "SUCCEEDED"


def a_lost_begin_keeps_the_credential_and_finds_its_transaction() -> None:
    """The window the previous design lost outright."""
    clean_scene()
    session().call("scene.snapshot")

    drop_response_to("transaction.begin")
    FINDINGS["begin_error"] = lost(lambda: session().begin_transaction("lost ack"))
    disarm()
    FINDINGS["begin_pending_credential"] = session().pending_begins()

    # The host applied it: a transaction exists, and the dead socket orphaned it.
    recoverable = session().recoverable_transaction()
    FINDINGS["begin_orphan_found"] = recoverable is not None
    FINDINGS["begin_orphan_state"] = (recoverable or {}).get("state")
    transaction = (recoverable or {}).get("transaction")
    FINDINGS["begin_credential_bound"] = session().holds_credential_for(transaction or "")
    FINDINGS["begin_pending_after_bind"] = session().pending_begins()

    adopted = session().adopt_transaction(transaction)
    FINDINGS["begin_adopt_ok"] = adopted.get("ok")
    session().end_transaction("transaction.discard", transaction)


def a_lost_adoption_that_applied_promotes_the_replacement() -> None:
    """The host rotated; the client must know its next secret is now current."""
    clean_scene()
    session().call("scene.snapshot")
    transaction = session().begin_transaction("rotated")["result"]["transaction"]
    session().call("object.create", {"kind": "cube", "name": "Rotated"})

    session().close()          # orphan it
    time.sleep(0.05)
    session().recoverable_transaction()

    drop_response_to("transaction.adopt")
    FINDINGS["rotate_error"] = lost(lambda: session().adopt_transaction(transaction))
    disarm()

    state = session().recoverable_transaction()
    FINDINGS["rotate_generation"] = state["credential_generation"]
    FINDINGS["rotate_reconciled"] = state["credential_reconciled"]
    # The credential it now holds must be the one the host expects.
    again = session().adopt_transaction(transaction)
    FINDINGS["rotate_readopt_ok"] = again.get("ok")
    session().end_transaction("transaction.discard", transaction)


def a_lost_adoption_that_never_applied_keeps_the_original() -> None:
    """The connection died before the host rotated; the old secret still works."""
    clean_scene()
    session().call("scene.snapshot")
    transaction = session().begin_transaction("unrotated")["result"]["transaction"]
    session().call("object.create", {"kind": "cube", "name": "Unrotated"})

    session().close()
    time.sleep(0.05)
    session().recoverable_transaction()

    # Killed in flight: the request never reaches the host, so nothing rotates.
    drop_request_to("transaction.adopt")
    FINDINGS["unrotated_error"] = lost(lambda: session().adopt_transaction(transaction))
    disarm()

    state = session().recoverable_transaction()
    FINDINGS["unrotated_generation"] = state["credential_generation"]
    FINDINGS["unrotated_reconciled"] = state["credential_reconciled"]
    again = session().adopt_transaction(transaction)
    FINDINGS["unrotated_readopt_ok"] = again.get("ok")
    session().end_transaction("transaction.discard", transaction)


def public_status(transaction: str | None = None) -> dict:
    """What an agent calls: the MCP transaction status tool, by its own path."""
    if transaction:
        return {"ok": True, "transaction": session().terminal_state(transaction)}
    return {"ok": True, "transaction": session().recoverable_transaction()}


def a_lost_terminal_reply_is_read_not_repeated(method: str, prefix: str, outcome: str) -> None:
    """The work finished; the outcome is retrieved rather than re-applied.

    Resolved through the public transaction status surface — the path an agent
    actually has — rather than only through the session helper underneath it.
    """
    clean_scene()
    session().call("scene.snapshot")
    transaction = session().begin_transaction(prefix)["result"]["transaction"]
    session().call("object.create", {"kind": "cube", "name": prefix.title()})
    before = len(bpy.data.objects)

    drop_response_to(method)
    FINDINGS[f"{prefix}_error"] = lost(
        lambda: session().end_transaction(method, transaction))
    disarm()

    resolved = public_status(transaction)["transaction"]
    FINDINGS[f"{prefix}_outcome"] = resolved["outcome"]
    FINDINGS[f"{prefix}_evidence"] = sorted(resolved["finished"] or {})
    FINDINGS[f"{prefix}_no_repeat"] = len(bpy.data.objects) == before
    FINDINGS[f"{prefix}_credential_dropped"] = not session().holds_credential_for(transaction)
    FINDINGS[f"{prefix}_expected_outcome"] = outcome


def conversation(port: int) -> None:
    os.environ["ROBOVISION_BLENDER_PORT"] = str(port)
    a_lost_begin_keeps_the_credential_and_finds_its_transaction()
    a_lost_adoption_that_applied_promotes_the_replacement()
    a_lost_adoption_that_never_applied_keeps_the_original()
    # Both a discard and a commit: the commit is the one whose evidence a
    # caller most needs, and background Blender can perform it — unlike a
    # rollback, whose restoration it cannot verify at all.
    a_lost_terminal_reply_is_read_not_repeated("transaction.discard", "discarded", "abandoned")
    a_lost_terminal_reply_is_read_not_repeated("transaction.commit", "committed", "committed")
    session().close()


def main() -> None:
    global RUNTIME
    clean_scene()
    RUNTIME = RoboVisionRuntime()
    port = free_port()
    RUNTIME.start(port=port)
    if bpy.app.timers.is_registered(RUNTIME._timer_fn):
        bpy.app.timers.unregister(RUNTIME._timer_fn)

    failure: list[BaseException] = []

    def worker() -> None:
        try:
            conversation(port)
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=worker, name="ack-loss", daemon=True)
    thread.start()
    try:
        pump(RUNTIME, thread)
    finally:
        RUNTIME.stop()
    if failure:
        raise failure[0]

    expect(FINDINGS["begin_error"] == "SESSION_LOST",
           f"a dropped begin reply was not surfaced: {FINDINGS['begin_error']}")
    expect(FINDINGS["begin_pending_credential"] == 1,
           "the credential did not survive the begin whose reply was lost")
    expect(FINDINGS["begin_orphan_found"], "the orphan the host opened was not found")
    expect(FINDINGS["begin_orphan_state"] == "orphaned",
           f"the transaction was not orphaned: {FINDINGS['begin_orphan_state']}")
    expect(FINDINGS["begin_credential_bound"],
           "the surviving credential was not bound to the transaction the host opened")
    expect(FINDINGS["begin_pending_after_bind"] == 0, "the pending credential was not consumed")
    expect(FINDINGS["begin_adopt_ok"], "the recovered credential could not adopt its transaction")

    expect(FINDINGS["rotate_error"] == "SESSION_LOST",
           f"a dropped adoption reply was not surfaced: {FINDINGS['rotate_error']}")
    expect(FINDINGS["rotate_generation"] == 1,
           f"the client did not learn the host had rotated: {FINDINGS['rotate_generation']}")
    expect(FINDINGS["rotate_reconciled"], "the credential was left ambiguous after reconciliation")
    expect(FINDINGS["rotate_readopt_ok"],
           "the promoted credential was not the one the host expects")

    expect(FINDINGS["unrotated_generation"] == 0,
           f"a rotation that never happened was assumed: {FINDINGS['unrotated_generation']}")
    expect(FINDINGS["unrotated_reconciled"], "the unrotated credential was left ambiguous")
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

    (artifact_dir("blender-ack-loss") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_ACK_LOSS", main, "blender-ack-loss")
