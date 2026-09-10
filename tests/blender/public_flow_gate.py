"""The shared cross-editor flow, run against Blender over a real socket.

Nothing host-specific lives here. The claims are in `tests/public_flow.py` and
are run unchanged against Unity by `tests/unity/public_flow.py`; this file only
stands a real Blender host up on a port, hands the shared module the same public
`HostSession` the MCP adapter holds, and pumps the transport while the two talk.

If the two hosts ever diverge on what they publish, on the health vocabulary, or
on whether an external agent can construct a pinned autonomous call without
reaching into a package, exactly one of these two thin wrappers goes red and the
shared module says which claim broke.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import bpy

# The shared module lives in `tests/`, this wrapper in `tests/blender/`, and the
# two are deliberately named differently: a gate that shadowed the claims it was
# meant to run would import itself and pass by doing nothing.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import artifact_dir, clean_scene, expect, run_gate  # noqa: E402
import public_flow  # noqa: E402
from robovision import mcp_server  # noqa: E402
from robovision_blender.runtime import RoboVisionRuntime  # noqa: E402

REPORT: dict = {}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def pump(runtime, worker, timeout: float = 180.0) -> None:
    """Service the socket from the main thread while the client talks to it.

    Blender's timer never runs under `--python`, so the host is driven explicitly
    rather than by sleeping and hoping one fires.
    """
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        runtime.transport.poll(runtime.dispatch, command_budget=8)
        if time.monotonic() > deadline:
            raise AssertionError("the public flow gate timed out")
        time.sleep(0.001)
    for _ in range(50):
        runtime.transport.poll(runtime.dispatch, command_budget=8)


def background_is_reported_rather_than_hidden(session, found) -> None:
    """The two things this editor can prove that the shared claims must not assume.

    Background Blender has no interactive VIEW_3D and cannot prove a rollback.
    Those are different missing mechanisms belonging to different capabilities,
    and both must reach the client as their own blocked answer — not as a degraded
    host, and not as a silence the agent discovers by attempting a capture or by
    finding a rejected candidate still in the scene.

    Everything else stays ready beside them. An agent that read this as "Blender
    is unwell" would stop working in an editor that can observe, author and
    verify perfectly well.
    """
    report = (session.health().get("result") or {})["ready_for"]
    for name in ("visual_observation", "semantic_observation", "mutate",
                 "semantic_verify", "begin_correction"):
        found.record("background_" + name, report[name]["status"])
    found.record("background_visual_reason", report["visual_observation"].get("reason"))
    found.record("background_begin_reason", report["begin_correction"].get("reason"))
    found.record("background_begin_verified_rollback",
                 report["begin_correction"].get("verified_rollback"))

    found.expect(report["visual_observation"]["status"] == "blocked",
                 "background Blender claimed an interactive viewport over the wire")
    found.expect(report["begin_correction"]["status"] == "blocked",
                 "a correction with no reject branch was offered over the wire")
    found.expect(report["begin_correction"].get("reason") == "verified_rollback_unavailable",
                 "the missing correction mechanism was not named over the wire")
    found.expect(report["begin_correction"].get("verified_rollback") is False,
                 "the block did not state the missing mechanism as a fact")
    found.expect(report["semantic_observation"]["status"] == "ready",
                 "a missing viewport was allowed to condemn the semantic read")
    found.expect(report["semantic_verify"]["status"] == "ready",
                 "blocking a correction was allowed to condemn semantic verification")
    found.expect(report["mutate"]["status"] == "ready",
                 "background Blender was told over the wire that it could not author")


def conversation(port: int) -> None:
    os.environ["ROBOVISION_BLENDER_PORT"] = str(port)
    session = mcp_server._session("blender")
    REPORT.update(public_flow.run(session, "blender",
                                  extra=background_is_reported_rather_than_hidden))
    # The whole flow ran on one connection: a health call that opened a second
    # socket would describe a caller that no longer exists by the time anyone
    # acts on it, and would orphan whatever the first one was holding.
    REPORT["connection_generation"] = session.generation
    session.close()


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
        except BaseException as exc:  # noqa: BLE001 - reported, then re-raised
            failure.append(exc)

    thread = threading.Thread(target=worker, name="public-flow", daemon=True)
    thread.start()
    try:
        pump(runtime, thread)
    finally:
        runtime.stop()
    if failure:
        raise failure[0]

    (artifact_dir("blender-public-flow") / "report.json").write_text(
        json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8")

    expect(REPORT.get("ok") is True,
           "the shared cross-editor flow failed against Blender:\n"
           + "\n".join(REPORT.get("failures", [])))
    expect(REPORT.get("connection_generation") == 1,
           f"health did not reuse the persistent session connection: "
           f"{REPORT.get('connection_generation')}")


run_gate("BLENDER_PUBLIC_FLOW", main, "blender-public-flow")
