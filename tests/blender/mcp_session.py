"""The Astra-facing path, driven the way Astra drives it.

Measured before this existed, through the real `mcp_server._call`: a
`transaction.begin` succeeded, and by the next MCP tool call the transaction was
already orphaned, because the connection that opened it closed as that call
returned. The mutation and the rollback were both refused
`TRANSACTION_ORPHANED`. The host's ownership guarantee was correct and the
adapter could not hold a transaction long enough to use one — which made
transactions unusable for the Artist Loop.

Two flows here: the ordinary one, and the one where the connection dies mid
transaction and the work has to be reclaimed rather than assumed.
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


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def pump(runtime, worker, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        runtime.transport.poll(runtime.dispatch, command_budget=8)
        if time.monotonic() > deadline:
            raise AssertionError("mcp session gate timed out")
        time.sleep(0.001)
    for _ in range(50):
        runtime.transport.poll(runtime.dispatch, command_budget=8)


def call(method: str, params: dict | None = None, **fields) -> dict:
    """Exactly the path an MCP tool call takes."""
    return mcp_server._call("blender", method, params or {}, fields.pop("if_revision", None), **fields)


def failure_of(fn) -> dict:
    """Run a call that must be refused, and return its error payload.

    The session layer raises; the MCP tool layer is what turns that into a dict
    for a model to read. Testing the session directly means catching.
    """
    try:
        fn()
    except RoboVisionError as exc:
        return {"code": exc.payload.code, "data": exc.payload.data or {}}
    return {"code": "SUCCEEDED", "data": {}}


def code_of(fn) -> str:
    try:
        fn()
    except RoboVisionError as exc:
        return exc.payload.code
    return "SUCCEEDED"


def session():
    return mcp_server._session("blender")


def a_transaction_survives_across_tool_calls() -> None:
    """begin, mutate, read, roll back — each a separate MCP call."""
    baseline = call("scene.snapshot")["result"]["fingerprint"]
    # Through the public helper: nothing here knows a credential exists.
    begun = session().begin_transaction("artist loop")["result"]
    FINDINGS["precommitted"] = begun.get("recovery_precommitted")
    FINDINGS["secret_in_reply"] = "recovery_token" in begun
    tx = begun["transaction"]

    state = call("system.hello")["result"]["transaction"]["state"]
    FINDINGS["state_between_calls"] = state["state"]
    FINDINGS["owner_disconnected_between_calls"] = state["owner_disconnected"]

    for index in range(3):
        created = call("object.create", {"kind": "cube", "name": f"Loop{index}"})
        FINDINGS[f"mutate_{index}"] = created["ok"]

    described = call("scene.describe")["result"]
    FINDINGS["read_during_transaction"] = len(described["objects"])

    # The rollback is attempted as the owner. Background Blender cannot verify
    # one — asserted by its own gate — so what this proves is the boundary that
    # was broken: the call reaches the host as the transaction's owner rather
    # than as a stranger. UNDO_UNAVAILABLE means the mechanism is missing;
    # TRANSACTION_ORPHANED would mean the session was.
    try:
        rolled = session().end_transaction("transaction.rollback", tx)["result"]
        FINDINGS["rollback_code"] = "OK"
        FINDINGS["rollback_ok"] = rolled["rolled_back"]
        FINDINGS["restored"] = call("scene.snapshot")["result"]["fingerprint"] == baseline
    except RoboVisionError as exc:
        FINDINGS["rollback_code"] = exc.payload.code
        FINDINGS["rollback_ok"] = None
        FINDINGS["restored"] = None
        session().end_transaction("transaction.discard", tx)


def a_dead_connection_is_reclaimed_rather_than_assumed() -> None:
    """The socket dies mid transaction; the work is adopted, not inherited."""
    begun = session().begin_transaction("interrupted")["result"]
    tx = begun["transaction"]
    call("object.create", {"kind": "cube", "name": "Interrupted"})

    # The connection dies underneath the adapter.
    session().close()
    time.sleep(0.05)

    # The session asks the host what became of the work rather than assuming.
    recoverable = session().recoverable_transaction()
    FINDINGS["after_death_state"] = recoverable["state"]
    FINDINGS["after_death_adoption_required"] = recoverable["adoption_required"]
    FINDINGS["after_death_recoverable"] = recoverable["recoverable_by_this_session"]
    FINDINGS["credential_survived_socket"] = session().holds_credential_for(tx)

    # The new connection is a stranger, and the adapter does not pretend it is
    # the old owner.
    FINDINGS["stranger_mutate_code"] = code_of(
        lambda: call("object.create", {"kind": "cube", "name": "Assumed"}))
    FINDINGS["stranger_rollback_code"] = code_of(
        lambda: call("transaction.rollback", {"transaction": tx}))

    adopted = session().adopt_transaction(tx)["result"]
    FINDINGS["adopted_state"] = adopted["state"]
    FINDINGS["adopt_precommitted"] = adopted.get("recovery_precommitted")
    FINDINGS["adopt_secret_in_reply"] = "recovery_token" in adopted

    FINDINGS["continue_after_adopt"] = call(
        "object.create", {"kind": "cube", "name": "Continued"})["ok"]
    FINDINGS["finish_after_adopt"] = session().end_transaction(
        "transaction.discard", tx)["result"]["state"]
    FINDINGS["credential_dropped_after_finish"] = not session().holds_credential_for(tx)

    # A session that did not open a transaction holds nothing for it.
    try:
        session().adopt_transaction("rvtx:nope:nope")
        FINDINGS["adopt_without_credential"] = "SUCCEEDED"
    except RoboVisionError as exc:
        FINDINGS["adopt_without_credential"] = exc.payload.code


def the_strict_contract_is_reachable_from_the_public_path() -> None:
    described = call("scene.describe")["result"]
    hello = call("system.hello")["result"]
    accepted = call(
        "object.create", {"kind": "cube", "name": "Contracted"},
        if_revision=described["revision"],
        idempotency_key="mcp-strict",
        attempt=1,
        expected_world=described["world_incarnation"],
        expected_coordinate_contract=hello["coordinate_contract"],
        contract="autonomous",
    )
    FINDINGS["strict_ok"] = accepted["ok"]

    # And the retry of it replays rather than creating a second object.
    replay = call(
        "object.create", {"kind": "cube", "name": "Contracted"},
        if_revision=accepted["revision"],
        idempotency_key="mcp-strict",
        attempt=2,
        expected_world=described["world_incarnation"],
        expected_coordinate_contract=hello["coordinate_contract"],
        contract="autonomous",
    )
    FINDINGS["strict_replayed"] = replay.get("replayed")
    FINDINGS["strict_original_recorded"] = bool(replay.get("original_execution"))
    FINDINGS["objects_after_retry"] = len(
        [o for o in call("scene.describe")["result"]["objects"] if o["name"] == "Contracted"])


def a_planned_transaction_is_pinned_to_its_observation() -> None:
    """A checkpoint is only worth anything if it is the state you planned against."""
    described = call("scene.describe")["result"]
    hello = call("system.hello")["result"]
    planned_revision = described["revision"]

    # A human edits the scene between the observation and the transaction.
    call("object.create", {"kind": "cube", "name": "HumanEdit"})
    moved = call("scene.describe")["result"]["revision"]
    FINDINGS["scene_moved"] = moved != planned_revision

    stale = failure_of(lambda: session().begin_transaction(
        "planned", if_revision=planned_revision,
        expected_world=described["world_incarnation"],
        expected_coordinate_contract=hello["coordinate_contract"],
        contract="autonomous"))
    FINDINGS["stale_begin_code"] = stale["code"]
    FINDINGS["no_transaction_opened"] = (
        call("system.hello")["result"]["transaction"]["active"] is False)

    # The same plan, refreshed, opens normally.
    fresh = call("scene.describe")["result"]
    good = session().begin_transaction(
        "planned", if_revision=fresh["revision"],
        expected_world=fresh["world_incarnation"],
        expected_coordinate_contract=hello["coordinate_contract"],
        contract="autonomous")
    FINDINGS["fresh_begin_ok"] = good.get("ok")
    tx = good["result"]["transaction"]

    # Its terminal record still carries the proof that made it terminal.
    session().end_transaction("transaction.discard", tx)
    ended = failure_of(lambda: call("transaction.commit", {"transaction": tx}))
    FINDINGS["terminal_code"] = ended["code"]
    FINDINGS["terminal_evidence"] = sorted(ended["data"])

    # And an autonomous begin missing its pins is refused before opening one.
    bare = failure_of(lambda: session().begin_transaction("unpinned", contract="autonomous"))
    FINDINGS["unpinned_begin_code"] = bare["code"]
    FINDINGS["unpinned_missing"] = sorted(bare["data"].get("missing", []))


def conversation(port: int) -> None:
    os.environ["ROBOVISION_BLENDER_PORT"] = str(port)
    a_transaction_survives_across_tool_calls()
    a_dead_connection_is_reclaimed_rather_than_assumed()
    the_strict_contract_is_reachable_from_the_public_path()
    a_planned_transaction_is_pinned_to_its_observation()
    mcp_server._session("blender").close()


def main() -> None:
    clean_scene()
    runtime = RoboVisionRuntime()
    port = free_port()
    runtime.start(port=port)
    if bpy.app.timers.is_registered(runtime._timer_fn):
        bpy.app.timers.unregister(runtime._timer_fn)

    failure: list[BaseException] = []

    def worker() -> None:
        try:
            conversation(port)
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=worker, name="mcp-session", daemon=True)
    thread.start()
    try:
        pump(runtime, thread)
    finally:
        runtime.stop()
    if failure:
        raise failure[0]

    expect(FINDINGS["precommitted"] is True, "the client's precommitted credential was not used")
    expect(FINDINGS["secret_in_reply"] is False,
           "a precommitted begin still returned a secret in its reply")
    expect(FINDINGS["state_between_calls"] == "active",
           f"the transaction did not survive to the next MCP call: {FINDINGS['state_between_calls']}")
    expect(FINDINGS["owner_disconnected_between_calls"] is False,
           "the owning connection closed between MCP calls")
    for index in range(3):
        expect(FINDINGS[f"mutate_{index}"], f"mutation {index} inside the transaction failed")
    expect(FINDINGS["read_during_transaction"] == 3, "reads during the transaction were wrong")
    expect(FINDINGS["rollback_code"] in ("OK", "UNDO_UNAVAILABLE"),
           f"the rollback failed for an ownership reason: {FINDINGS['rollback_code']}")
    if FINDINGS["rollback_code"] == "OK":
        expect(FINDINGS["restored"], "the rollback did not restore the begin fingerprint")

    expect(FINDINGS["after_death_state"] == "orphaned",
           "a dead connection did not orphan the transaction")
    expect(FINDINGS["after_death_adoption_required"] is True, "the orphan did not ask to be adopted")
    expect(FINDINGS["stranger_mutate_code"] == "TRANSACTION_ORPHANED",
           f"the reconnected session was treated as the old owner: {FINDINGS['stranger_mutate_code']}")
    expect(FINDINGS["stranger_rollback_code"] == "TRANSACTION_ORPHANED",
           "a reconnected session finished work it had not proved it owned")
    expect(FINDINGS["adopted_state"] == "active", "the recovery secret did not adopt the transaction")
    expect(FINDINGS["adopt_precommitted"] is True, "adoption did not rotate to the precommitted secret")
    expect(FINDINGS["adopt_secret_in_reply"] is False,
           "adoption returned a secret the client had already committed to")
    expect(FINDINGS["continue_after_adopt"], "the adopter could not continue the transaction")
    expect(FINDINGS["finish_after_adopt"] == "abandoned", "the adopter could not finish it")

    expect(FINDINGS["strict_ok"], "the autonomous contract was not reachable through MCP")
    expect(FINDINGS["strict_replayed"] is True, "a strict retry executed instead of replaying")
    expect(FINDINGS["strict_original_recorded"], "the replay did not report the original execution")
    expect(FINDINGS["objects_after_retry"] == 1,
           f"the retry created a second object: {FINDINGS['objects_after_retry']}")

    expect(FINDINGS["after_death_recoverable"] is True,
           "the session could not tell that its own transaction was recoverable")
    expect(FINDINGS["credential_survived_socket"] is True,
           "the recovery credential died with the socket it was meant to outlive")
    expect(FINDINGS["credential_dropped_after_finish"] is True,
           "a finished transaction's credential was retained")
    expect(FINDINGS["adopt_without_credential"] == "NO_RECOVERY_CREDENTIAL",
           f"a session adopted a transaction it never opened: {FINDINGS['adopt_without_credential']}")

    expect(FINDINGS["scene_moved"], "precondition: the scene must move between plan and begin")
    expect(FINDINGS["stale_begin_code"] == "STALE_REVISION",
           f"a transaction opened against a scene that had moved: {FINDINGS['stale_begin_code']}")
    expect(FINDINGS["no_transaction_opened"],
           "the refused begin still opened a transaction")
    expect(FINDINGS["fresh_begin_ok"], "a correctly pinned begin was refused")
    expect(FINDINGS["terminal_code"] == "TRANSACTION_ABANDONED",
           f"a finished transaction was not reported as such: {FINDINGS['terminal_code']}")
    for proof in ("state", "reason", "world_incarnation"):
        expect(proof in FINDINGS["terminal_evidence"],
               f"the terminal record did not carry {proof}: {FINDINGS['terminal_evidence']}")
    expect(FINDINGS["unpinned_begin_code"] == "CONTRACT_VIOLATION",
           f"an unpinned autonomous begin was accepted: {FINDINGS['unpinned_begin_code']}")
    for field in ("expected_world", "expected_coordinate_contract", "if_revision"):
        expect(field in FINDINGS["unpinned_missing"],
               f"the violation did not name {field}: {FINDINGS['unpinned_missing']}")

    # No credential may appear anywhere a caller or a log can see.
    leaked = [key for key, value in FINDINGS.items() if "recovery_token" in str(value)]
    expect(not leaked, f"a recovery credential appeared in structured output: {leaked}")

    (artifact_dir("blender-mcp-session") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_MCP_SESSION", main, "blender-mcp-session")
