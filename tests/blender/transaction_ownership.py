"""Who owns a Blender transaction, proven over real connections.

Ownership cannot be tested in-process: every in-process call looks like the same
local client, so the one thing being asserted is the one thing that harness
cannot see. This gate opens real TCP connections with the shipped client, which
is also what makes a *disconnect* mean anything.

The editor-thread rule is upheld exactly as in gate 0: worker threads perform
socket I/O only and never touch bpy, while Blender's main thread pumps
`transport.poll()` as the add-on's timer does.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from robovision_blender.runtime import RoboVisionRuntime  # noqa: E402
from robovision.client import RoboVisionClient  # noqa: E402
from robovision.errors import RoboVisionError  # noqa: E402

FINDINGS: dict[str, object] = {}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def pump(runtime: RoboVisionRuntime, worker: threading.Thread, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        runtime.transport.poll(runtime.dispatch, command_budget=8)
        if time.monotonic() > deadline:
            raise AssertionError("ownership gate timed out waiting for the client thread")
        time.sleep(0.001)
    for _ in range(50):
        runtime.transport.poll(runtime.dispatch, command_budget=8)


def res(client, method: str, params: dict | None = None) -> dict:
    """The result body. The shipped client returns the whole envelope."""
    return client.call(method, params or {})["result"]


def code_of(call) -> str:
    """Run a call that must fail, and report the code it failed with."""
    try:
        call()
    except RoboVisionError as exc:
        return exc.payload.code
    return "SUCCEEDED"


def conversation(port: int) -> None:
    """The whole ownership story, from two connections that are really separate."""
    owner = RoboVisionClient(port=port)
    owner.connect()
    begun = res(owner, "transaction.begin", {"label": "owned"})
    FINDINGS["transaction"] = begun["transaction"]
    FINDINGS["token_issued"] = bool(begun.get("recovery_token"))
    FINDINGS["world_scoped_id"] = str(begun["transaction"]).startswith("rvtx:")
    token = begun["recovery_token"]
    res(owner, "object.create", {"kind": "cube", "name": "OwnedWork"})

    # A second connection: reads yes, mutations no.
    other = RoboVisionClient(port=port)
    other.connect()
    FINDINGS["other_read_ok"] = bool(res(other, "scene.describe")["objects"] is not None)
    FINDINGS["other_mutate_code"] = code_of(
        lambda: other.call("object.create", {"kind": "cube", "name": "Intruder"})
    )
    FINDINGS["other_commit_code"] = code_of(
        lambda: other.call("transaction.commit", {"transaction": begun["transaction"]})
    )
    FINDINGS["other_rollback_code"] = code_of(
        lambda: other.call("transaction.rollback", {"transaction": begun["transaction"]})
    )
    FINDINGS["owner_mutate_ok"] = bool(
        res(owner, "object.create", {"kind": "cube", "name": "AlsoOwned"})["id"]
    )

    # The owner goes away.
    owner.close()
    time.sleep(0.05)
    for _ in range(20):
        time.sleep(0.01)
        state = res(other, "system.hello")["transaction"]
        if state["active"] and state["state"]["owner_disconnected"]:
            break
    FINDINGS["orphaned_reported"] = state["state"]["owner_disconnected"]
    FINDINGS["adoption_required"] = state["state"]["adoption_required"]
    FINDINGS["hello_leaks_token"] = "recovery_token" in str(state)

    # A stranger still cannot finish it, orphan or not.
    FINDINGS["orphan_mutate_code"] = code_of(
        lambda: other.call("object.create", {"kind": "cube", "name": "Sneaky"})
    )
    FINDINGS["orphan_commit_code"] = code_of(
        lambda: other.call("transaction.commit", {"transaction": begun["transaction"]})
    )
    FINDINGS["bad_token_code"] = code_of(
        lambda: other.call("transaction.adopt", {
            "transaction": begun["transaction"], "recovery_token": "not-the-token"
        })
    )
    after_refusal = res(other, "system.hello")["transaction"]["state"]
    FINDINGS["refusal_changed_nothing"] = (
        after_refusal["owner_disconnected"] and after_refusal["transaction"] == begun["transaction"]
    )

    # The owner reconnects from a new connection and proves itself.
    heir = RoboVisionClient(port=port)
    heir.connect()
    adopted = res(heir, "transaction.adopt", {
        "transaction": begun["transaction"], "recovery_token": token
    })
    FINDINGS["adopted_state"] = adopted["state"]
    FINDINGS["token_rotated"] = adopted.get("recovery_token") not in (None, token)
    FINDINGS["adopter_mutate_ok"] = bool(
        res(heir, "object.create", {"kind": "cube", "name": "ByHeir"})["id"]
    )
    # And the stranger is a stranger again, now that the transaction has an owner.
    FINDINGS["other_after_adopt_code"] = code_of(
        lambda: other.call("object.create", {"kind": "cube", "name": "StillIntruding"})
    )
    FINDINGS["stale_token_code"] = code_of(
        lambda: other.call("transaction.adopt", {
            "transaction": begun["transaction"], "recovery_token": token
        })
    )

    # Background Blender cannot verify a rollback, so this ends by discarding.
    FINDINGS["discard_state"] = res(
        heir, "transaction.discard", {"transaction": begun["transaction"]}
    )["state"]
    FINDINGS["other_after_discard_ok"] = bool(
        res(other, "object.create", {"kind": "cube", "name": "Free"})["id"]
    )
    heir.close()
    other.close()


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
        except BaseException as exc:  # surfaced on the main thread below
            failure.append(exc)

    thread = threading.Thread(target=worker, name="ownership-client", daemon=True)
    thread.start()
    try:
        pump(runtime, thread)
    finally:
        runtime.stop()
    if failure:
        raise failure[0]

    expect(FINDINGS["token_issued"], "begin issued no recovery token")
    expect(FINDINGS["world_scoped_id"],
           f"the transaction id is not scoped to its world: {FINDINGS['transaction']}")
    expect(FINDINGS["other_read_ok"], "reads must stay available during a transaction")
    expect(FINDINGS["other_mutate_code"] == "TRANSACTION_FOREIGN",
           f"a second connection joined a transaction it did not open: {FINDINGS['other_mutate_code']}")
    expect(FINDINGS["other_commit_code"] == "TRANSACTION_FOREIGN",
           f"a second connection committed another's transaction: {FINDINGS['other_commit_code']}")
    expect(FINDINGS["other_rollback_code"] == "TRANSACTION_FOREIGN",
           f"a second connection rolled back another's work: {FINDINGS['other_rollback_code']}")
    expect(FINDINGS["owner_mutate_ok"], "the owner could not use its own transaction")

    expect(FINDINGS["orphaned_reported"], "a disconnect did not orphan the transaction")
    expect(FINDINGS["adoption_required"], "the orphan did not say it needed adopting")
    expect(not FINDINGS["hello_leaks_token"], "system.hello exposed the recovery credential")
    expect(FINDINGS["orphan_mutate_code"] == "TRANSACTION_ORPHANED",
           f"a stranger continued an orphan: {FINDINGS['orphan_mutate_code']}")
    expect(FINDINGS["orphan_commit_code"] == "TRANSACTION_ORPHANED",
           f"a stranger finished an orphan: {FINDINGS['orphan_commit_code']}")
    expect(FINDINGS["bad_token_code"] == "TRANSACTION_ADOPTION_REFUSED",
           f"a wrong token was not refused cleanly: {FINDINGS['bad_token_code']}")
    expect(FINDINGS["refusal_changed_nothing"], "a refused adoption changed the transaction")

    expect(FINDINGS["adopted_state"] == "active", f"adoption left it {FINDINGS['adopted_state']}")
    expect(FINDINGS["token_rotated"], "adoption did not rotate the recovery token")
    expect(FINDINGS["adopter_mutate_ok"], "the adopter could not use the transaction it now owns")
    expect(FINDINGS["other_after_adopt_code"] == "TRANSACTION_FOREIGN",
           "the transaction did not belong to its adopter")
    expect(FINDINGS["stale_token_code"] == "TRANSACTION_ADOPTION_REFUSED",
           f"a token retired by adoption still worked: {FINDINGS['stale_token_code']}")
    expect(FINDINGS["discard_state"] == "abandoned", "discard did not end the transaction")
    expect(FINDINGS["other_after_discard_ok"], "the editor stayed wedged after the transaction ended")

    (artifact_dir("blender-transaction-ownership") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8",
    )


run_gate("BLENDER_TRANSACTION_OWNERSHIP", main, "blender-transaction-ownership")
