"""Gate 0 transport: drive the host the way a real client does.

The other gates call `runtime.dispatch` in-process, which proves the operations
but skips the socket, the newline framing, the size limits and the shipped
Python client entirely. This gate connects over TCP with `RoboVisionClient` and
exercises that path end to end.

The editor-thread rule is upheld: the worker thread performs socket I/O only and
never touches bpy, while Blender's main thread pumps `transport.poll()` exactly
as the add-on's timer does.
"""
from __future__ import annotations

import json
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
from robovision.protocol import PROTOCOL_VERSION  # noqa: E402


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def pump(runtime: RoboVisionRuntime, worker: threading.Thread, timeout: float = 120.0) -> None:
    """Service the socket from the main thread until the client is finished."""
    deadline = time.monotonic() + timeout
    while worker.is_alive():
        runtime.transport.poll(runtime.dispatch, command_budget=8)
        if time.monotonic() > deadline:
            raise AssertionError("transport gate timed out waiting for the client thread")
        time.sleep(0.001)
    # Drain anything queued as the worker finished.
    for _ in range(50):
        runtime.transport.poll(runtime.dispatch, command_budget=8)


def main() -> None:
    clean_scene()
    runtime = RoboVisionRuntime()
    port = free_port()
    runtime.start(port=port)
    # The add-on drives poll() from a bpy timer; this gate pumps it explicitly so
    # the test controls sequencing. Unregister the timer to keep one pump loop.
    if bpy.app.timers.is_registered(runtime._timer_fn):
        bpy.app.timers.unregister(runtime._timer_fn)

    findings: dict[str, object] = {}
    failures: list[str] = []

    def record(name: str, value: object) -> None:
        findings[name] = value

    def worker() -> None:
        try:
            with RoboVisionClient("127.0.0.1", port, timeout=30.0) as client:
                hello = client.call("system.hello")
                record("protocol", hello["result"]["protocol"])
                record("editor", hello["result"]["editor"]["name"])
                record("capability_count", hello["result"]["capability_count"])
                record("transport_port", hello["result"]["transport"]["port"])

                created = client.call("object.create", {"kind": "cube", "name": "Wire"})
                oid = created["result"]["id"]
                record("created_id_prefix", oid.split(":")[0])
                record("revision_after_create", created["revision"])
                record("timing_present", isinstance(created.get("timing_ms"), (int, float)))

                inspected = client.call("mesh.inspect", {"object": oid})
                record("faces", inspected["result"]["faces"])

                # A typed host error must arrive as a typed client error.
                try:
                    client.call("object.inspect", {"object": "b3d:does-not-exist"})
                    record("not_found_raised", False)
                except RoboVisionError as exc:
                    record("not_found_raised", True)
                    record("not_found_code", exc.payload.code)

                # Optimistic concurrency across the wire.
                try:
                    client.call("object.transform", {"object": oid, "location": [1, 2, 3]}, if_revision=0)
                    record("stale_revision_raised", False)
                except RoboVisionError as exc:
                    record("stale_revision_raised", True)
                    record("stale_revision_code", exc.payload.code)

                # Many sequential calls on one connection: framing must not drift.
                for index in range(40):
                    response = client.call("system.ping")
                    if response["result"]["pong"] is not True:
                        failures.append(f"ping {index} returned {response}")
                record("sequential_pings", 40)

            # Two pipelined requests in a single packet must produce two framed
            # replies in order.
            with socket.create_connection(("127.0.0.1", port), timeout=30.0) as raw:
                payload = b""
                for index in (1, 2):
                    payload += json.dumps(
                        {"rv": PROTOCOL_VERSION, "id": f"pipe-{index}", "method": "system.ping", "params": {}}
                    ).encode() + b"\n"
                raw.sendall(payload)
                buffer = bytearray()
                while buffer.count(b"\n") < 2:
                    chunk = raw.recv(65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                lines = [json.loads(line) for line in bytes(buffer).split(b"\n") if line.strip()]
                record("pipelined_ids", [entry.get("id") for entry in lines])

            # Malformed JSON must return a typed transport error, not a crash.
            with socket.create_connection(("127.0.0.1", port), timeout=30.0) as raw:
                raw.sendall(b"{not json at all}\n")
                buffer = bytearray()
                while b"\n" not in buffer:
                    chunk = raw.recv(65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                if buffer:
                    record("malformed_code", json.loads(bytes(buffer).split(b"\n")[0])["error"]["code"])

            # A request past the transport limit must be refused, not buffered.
            with socket.create_connection(("127.0.0.1", port), timeout=30.0) as raw:
                try:
                    raw.sendall(b"x" * (5 * 1024 * 1024))
                    buffer = bytearray()
                    raw.settimeout(10.0)
                    while b"\n" not in buffer:
                        chunk = raw.recv(65536)
                        if not chunk:
                            break
                        buffer.extend(chunk)
                    if buffer:
                        record("oversize_code", json.loads(bytes(buffer).split(b"\n")[0])["error"]["code"])
                    else:
                        record("oversize_code", "CONNECTION_CLOSED")
                except OSError as exc:
                    record("oversize_code", f"OSError:{type(exc).__name__}")

            # The host must still serve a fresh client afterwards.
            with RoboVisionClient("127.0.0.1", port, timeout=30.0) as client:
                record("survived_abuse", client.call("system.ping")["result"]["pong"])
        except Exception as exc:  # surfaced on the main thread
            failures.append(f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=worker, name="robovision-transport-gate", daemon=True)
    thread.start()
    try:
        pump(runtime, thread)
    finally:
        thread.join(timeout=5.0)
        runtime.stop()

    expect(not failures, f"client thread reported failures: {failures}")
    expect(findings.get("protocol") == PROTOCOL_VERSION, f"protocol mismatch over the wire: {findings}")
    expect(findings.get("editor") == "Blender", f"unexpected editor identity: {findings}")
    expect(int(findings.get("capability_count", 0)) > 0, "host advertised no capabilities")
    expect(findings.get("transport_port") == port, "host reported the wrong transport port")
    expect(findings.get("created_id_prefix") == "b3d", f"unexpected id form: {findings}")
    expect(findings.get("timing_present") is True, "responses carried no timing evidence")
    expect(findings.get("faces") == 6, f"cube did not arrive intact over the wire: {findings}")
    expect(findings.get("not_found_raised") is True, "missing object did not raise")
    expect(findings.get("not_found_code") == "NOT_FOUND", f"wrong error code: {findings}")
    expect(findings.get("stale_revision_raised") is True, "stale if_revision was accepted over the wire")
    expect(findings.get("stale_revision_code") == "STALE_REVISION", f"wrong staleness code: {findings}")
    expect(findings.get("pipelined_ids") == ["pipe-1", "pipe-2"], f"framing drifted: {findings}")
    expect(findings.get("malformed_code") == "INVALID_REQUEST", f"malformed JSON mishandled: {findings}")
    # The host refuses the oversized request either with its typed error or by
    # dropping the connection. Both are acceptable: once the peer is mid-send,
    # TCP can reset before the error is read. What must not happen is the host
    # buffering it, hanging, or dying, which the survival check below covers.
    expect(
        str(findings.get("oversize_code")).startswith(("MESSAGE_TOO_LARGE", "CONNECTION_CLOSED", "OSError:")),
        f"oversized request mishandled: {findings}",
    )
    expect(findings.get("survived_abuse") is True, "host stopped serving after malformed traffic")

    (artifact_dir("blender-gate0-transport") / "findings.json").write_text(
        json.dumps(findings, indent=2), encoding="utf-8"
    )
    print("  " + json.dumps(findings), flush=True)


run_gate("GATE0_TRANSPORT", main, "blender-gate0-transport")
