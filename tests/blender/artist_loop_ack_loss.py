"""The two Artist Loop recovery windows, driven through the real transport.

Both are end-to-end sequences that a unit test with stubbed sessions can only
approximate. They run over a real socket, with the response dropped by the
transport's own fault seam — after the handler ran, before delivery — so the
host really has applied the work the client never heard about.

**A lost mutation acknowledgement.** A model's mutation reaches the host and
applies; the reply is lost; the socket dies. The driver stops delivering, the
reads that follow reconnect, and the host has meanwhile orphaned the transaction
the dead connection owned. The rollback then comes back `TRANSACTION_ORPHANED` —
a definite answer, not a transport loss — and the first version of `fail_closed`
resolved definite answers by *reading*, so an active orphan holding the model's
mutation was reported `unresolved`. The scene would have kept the change while
the record said the attempt was indeterminate. It is adopted and rolled back.

**A lost final snapshot, after a commit.** Once the host has confirmed a commit,
the candidate's outcome is history. If evidence collection then fails, that is a
failure to record what happened, not a change in what happened. The attempt is
reported committed and incomplete — never relabelled as rolled back.

Interactive Blender: background `ed.undo` reports FINISHED and restores nothing,
so a verified rollback proved there would be theatre.
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
from robovision.artist_loop import Brief  # noqa: E402
from robovision.artist_loop_delivery import Delivery, Ledger, fail_closed  # noqa: E402
from robovision.artist_loop_run import Correction, Trajectory, run_correction  # noqa: E402
from robovision.errors import RoboVisionError  # noqa: E402
from robovision.session import HostSession  # noqa: E402
from robovision_blender.runtime import RoboVisionRuntime  # noqa: E402

FINDINGS: dict[str, object] = {}
RUNTIME: RoboVisionRuntime | None = None
WORK = Path(bpy.app.tempdir) / "robovision-artist-ack"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def pump(worker: threading.Thread, timeout: float = 180.0) -> None:
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        RUNTIME.transport.poll(RUNTIME.dispatch, command_budget=8)
        if time.monotonic() > deadline:
            raise AssertionError("artist loop ack gate timed out")
        time.sleep(0.001)
    for _ in range(50):
        RUNTIME.transport.poll(RUNTIME.dispatch, command_budget=8)


def drop_response_to(method: str, after: str | None = None) -> None:
    """Apply the next call to `method` and never answer it.

    `after` arms the fault only once another method has already been seen, which
    is how the final snapshot of a correction is dropped without touching the
    identical call at the start of it.
    """
    armed = {"used": False, "ready": after is None}

    def fault(request, _response):
        if request.get("method") == after:
            armed["ready"] = True
            return True
        if armed["used"] or not armed["ready"] or request.get("method") != method:
            return True
        armed["used"] = True
        return False

    RUNTIME.transport.fault_after_dispatch = fault


def disarm() -> None:
    RUNTIME.transport.fault_after_dispatch = None
    RUNTIME.transport.fault_before_dispatch = None


def result(response) -> dict:
    return response.get("result") or {}


def build(session: HostSession, widen: float) -> None:
    for obj in result(session.call("scene.describe"))["objects"]:
        session.call("object.delete", {"object": obj["name"]})
    # Both parts are distorted, so the correction has two mutations that are each
    # individually detectable — otherwise "the second one did not land" could not
    # be told apart from "the second one was a no-op".
    for name, scale, location in (("Part", (0.4 * widen, 0.3, 0.36), (0.0, 0.0, 0.0)),
                                  ("Cap", (0.2, 0.2, 0.12), (0.0, 0.0, 0.28 * widen))):
        session.call("object.create", {"kind": "cube", "name": name, "size": 1.0})
        session.call("object.transform", {"object": name, "scale": list(scale),
                                          "location": list(location)})


def brief_for(session: HostSession) -> Brief:
    """A correct asset, its front profile, then the same asset made too wide.

    The reference is produced by `truth.silhouette` over the public path, so the
    gate needs no image fixture and the comparison knows which parts the picture
    depicts.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    build(session, widen=1.0)
    names = sorted(obj["name"] for obj in
                   result(session.call("scene.describe"))["objects"])
    views = result(session.call("truth.views", {"objects": names, "level": 1}))
    view = max(views["cameras"], key=lambda c: -c["direction"][1])["view"]
    produced = result(session.call("truth.silhouette", {
        "objects": names, "view": view, "path": str(WORK / "front.png"),
        "width": 128, "height": 128}))
    build(session, widen=1.45)
    return Brief(
        name="ack bench", subjects=names,
        references=[{"key": "front", "path": produced["artifact"]["path"],
                     "view": view, "frame": {"center": produced["frame"]["center"],
                                             "radius": produced["frame"]["radius"]},
                     "subjects": produced["subjects"]}],
        required_invariants=["geometry.manifold", "geometry.normals_consistent"])


def narrow(objective: str) -> Correction:
    return Correction(
        objective=objective,
        reason="the front profile is too wide; restore the authored width",
        targets=[{"metric": "reference.macro.excess_fraction", "kind": "ref:front",
                  "epsilon": 0.02}],
        locality_targets=["Part", "Cap"],
        operations=[
            {"method": "object.transform",
             "params": {"object": "Part", "scale": [0.4, 0.3, 0.36],
                        "location": [0.0, 0.0, 0.0]}},
            {"method": "object.transform",
             "params": {"object": "Cap", "scale": [0.2, 0.2, 0.12],
                        "location": [0.0, 0.0, 0.28]}},
        ])


# ------------------------------------------------- a lost mutation acknowledgement

def a_lost_mutation_ack_is_adopted_and_rolled_back(session: HostSession) -> None:
    brief = brief_for(session)
    health = result(session.health())
    snapshot_response = session.call("scene.snapshot", {"level": "deep"})
    begin_fingerprint = result(snapshot_response)["fingerprint"]
    revision = int(snapshot_response["revision"])

    begun = session.begin_transaction(
        "lost mutation ack", if_revision=revision, contract="autonomous",
        expected_world=health["identity"]["world_incarnation"],
        expected_coordinate_contract=health["identity"]["coordinate_contract"])
    transaction = result(begun)["transaction"]

    delivery = Delivery(session, Ledger(WORK), attempt_id="attempt:ackloss",
                        world=health["identity"]["world_incarnation"],
                        contract=health["identity"]["coordinate_contract"],
                        revision=int(begun["revision"]))

    correction = narrow("lost mutation ack")
    # The host applies this one and never answers it.
    drop_response_to("object.transform")
    delivered = []
    for index, request in enumerate(correction.operations):
        try:
            delivered.append(delivery.send(index, request["method"], request["params"]))
        except RoboVisionError as exc:
            FINDINGS["ack_delivery_error"] = exc.payload.code
            # Exactly what `_attempt` does: stop delivering. The pins the rest of
            # the correction was planned against no longer describe anything.
            break
    disarm()
    FINDINGS["ack_operations_delivered"] = len(delivered)
    FINDINGS["ack_mutation_applied"] = (
        abs(bpy.data.objects["Part"].scale[0] - 0.4) < 1e-6)
    FINDINGS["ack_second_mutation_landed"] = (
        abs(bpy.data.objects["Cap"].location[2] - 0.28) < 1e-6)

    # The reads a driver takes next. They reconnect, and the host has already
    # orphaned the transaction the dead socket owned.
    FINDINGS["ack_generation_before"] = session.generation
    session.call("scene.describe")
    FINDINGS["ack_generation_after"] = session.generation
    state = result(session.call("system.hello")).get("transaction") or {}
    FINDINGS["ack_host_state"] = (state.get("state") or {}).get("state")

    recovery = fail_closed(session, transaction, RuntimeError("injected Q1 failure"))
    FINDINGS["ack_recovery"] = recovery["recovery"]
    FINDINGS["ack_steps"] = [step["step"] for step in recovery["steps"]]

    final = result(session.call("scene.snapshot", {"level": "deep"}))
    FINDINGS["ack_restored"] = final["fingerprint"] == begin_fingerprint
    after = result(session.call("system.hello")).get("transaction") or {}
    FINDINGS["ack_transaction_open"] = bool(after.get("state"))


# ------------------------------------------------ a lost snapshot after a commit

def a_lost_final_snapshot_keeps_the_commit(session: HostSession) -> None:
    brief = brief_for(session)
    trajectory = Trajectory(WORK / "post-terminal", brief, "gate")
    before = result(session.call("scene.snapshot", {"level": "deep"}))["fingerprint"]

    # Dropped only once a commit has already gone through, so the correction's
    # opening snapshot is untouched and the one that fails is the closing one.
    drop_response_to("scene.snapshot", after="transaction.commit")
    try:
        run_correction(session, brief, narrow("post terminal evidence loss"), trajectory)
    except RoboVisionError as exc:
        FINDINGS["post_error"] = exc.payload.code
    else:
        raise AssertionError("the dropped final snapshot did not surface")
    disarm()

    record = trajectory.attempts[-1]
    FINDINGS["post_phase"] = record["phase"]
    FINDINGS["post_outcome"] = record["outcome"]
    FINDINGS["post_decision"] = record["evaluation"]["decision"]
    FINDINGS["post_validity"] = record["benchmark_validity"]
    FINDINGS["post_confirmed"] = (record.get("terminal_confirmed") or {}).get("outcome")
    FINDINGS["post_final_fingerprint"] = record["final_fingerprint"]
    FINDINGS["post_restored_claim"] = record["restored"]

    final = result(session.call("scene.snapshot", {"level": "deep"}))
    FINDINGS["post_scene_changed"] = final["fingerprint"] != before
    FINDINGS["post_width_committed"] = abs(bpy.data.objects["Part"].scale[0] - 0.4) < 1e-6
    state = result(session.call("system.hello")).get("transaction") or {}
    FINDINGS["post_transaction_open"] = bool(state.get("state"))


def conversation(port: int) -> None:
    session = HostSession("127.0.0.1", port)
    try:
        a_lost_mutation_ack_is_adopted_and_rolled_back(session)
        a_lost_final_snapshot_keeps_the_commit(session)
    finally:
        session.close()


def check() -> None:
    expect(not bpy.app.background,
           "this gate proves a verified rollback and must run in interactive Blender")

    expect(FINDINGS["ack_delivery_error"] == "SESSION_LOST",
           f"the dropped mutation reply was not surfaced: {FINDINGS['ack_delivery_error']}")
    expect(FINDINGS["ack_mutation_applied"],
           "the host did not apply the mutation whose reply was dropped, so this "
           "is not the window the gate exists to cover")
    expect(FINDINGS["ack_operations_delivered"] == 0,
           "a delivery that lost its reply was recorded as successful")
    expect(not FINDINGS["ack_second_mutation_landed"],
           "a second mutation landed after the delivery failure")
    expect(FINDINGS["ack_generation_after"] > FINDINGS["ack_generation_before"],
           "the session did not reconnect, so the orphan window was never entered")
    expect(FINDINGS["ack_host_state"] == "orphaned",
           f"the host did not orphan the transaction: {FINDINGS['ack_host_state']}")
    expect(FINDINGS["ack_recovery"] == "adopted_and_rolled_back",
           f"the active orphan was not reclaimed: {FINDINGS['ack_recovery']}")
    expect("adopt" in FINDINGS["ack_steps"],
           f"recovery never adopted: {FINDINGS['ack_steps']}")
    expect(FINDINGS["ack_restored"],
           "the scene did not return to its transaction-begin fingerprint")
    expect(not FINDINGS["ack_transaction_open"],
           "a transaction was still open after recovery")

    expect(FINDINGS["post_error"] == "SESSION_LOST",
           f"the dropped final snapshot was not surfaced: {FINDINGS['post_error']}")
    expect(FINDINGS["post_phase"] == "post_terminal",
           f"a post-commit failure was recorded as pre-terminal: {FINDINGS['post_phase']}")
    expect(FINDINGS["post_outcome"] == "committed",
           f"a committed candidate was relabelled: {FINDINGS['post_outcome']}")
    expect(FINDINGS["post_decision"] == "accept",
           f"the candidate's decision was rewritten: {FINDINGS['post_decision']}")
    expect(FINDINGS["post_confirmed"] == "committed",
           f"the host's terminal state was not read back: {FINDINGS['post_confirmed']}")
    expect(FINDINGS["post_validity"] == "incomplete_evidence",
           f"the attempt was not marked incomplete: {FINDINGS['post_validity']}")
    expect(FINDINGS["post_final_fingerprint"] is None
           and FINDINGS["post_restored_claim"] is None,
           "a final fingerprint was invented for a snapshot that never arrived")
    expect(FINDINGS["post_scene_changed"] and FINDINGS["post_width_committed"],
           "the committed mutation is not in the scene, so 'committed' is not true")
    expect(not FINDINGS["post_transaction_open"],
           "a transaction was still open after the commit")


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
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            failure.append(exc)

    thread = threading.Thread(target=worker, name="artist-ack-loss", daemon=True)
    thread.start()
    try:
        pump(thread)
    finally:
        RUNTIME.stop()
    if failure:
        raise failure[0]

    check()
    (artifact_dir("artist-loop-ack-loss") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("ARTIST_LOOP_ACK_LOSS", main, "artist-loop-ack-loss")
