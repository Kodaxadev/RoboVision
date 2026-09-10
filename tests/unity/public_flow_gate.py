"""The shared cross-editor flow, run against Unity over a real socket.

The counterpart of `tests/blender/public_flow_gate.py`, and deliberately just as
thin. The claims live in `tests/public_flow.py` and run unchanged here, driven by
the public `HostSession` — the same object the MCP adapter holds — because the
question this gate exists to answer is whether an *external* model can drive
Unity.

That is a real gap and not a hypothetical one. Unity's in-process EditMode tests
can call `RoboVisionRecipe.CoordinateContract()` directly, so they would keep
passing for as long as the host enforced a contract it never published. Only a
client outside the package finds that out, which is why this runs in a separate
process talking over the socket.

Driven by `RoboVisionHealthGate`, which starts the host and pumps the transport
while this process talks to it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
PORT = int(sys.argv[2])
REPORT = Path(sys.argv[3])

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

import os  # noqa: E402

os.environ["ROBOVISION_UNITY_PORT"] = str(PORT)

import public_flow  # noqa: E402
from robovision import mcp_server  # noqa: E402


def batch_mode_is_reported_rather_than_hidden(session, found) -> None:
    """What this editor can prove that the shared claims must not assume.

    A batch-mode Unity has no SceneView, so the implemented capture path cannot
    run. That must reach the client as a blocked *visual* answer beside a ready
    semantic one, distinguished from "not implemented" — which is what the
    perception subsystem says about the multi-pass bundle Unity genuinely does
    not have. An agent that could not tell those apart would either give up on a
    working editor or retry a capability that will never exist.
    """
    report = (session.health().get("result") or {})
    ready_for = report["ready_for"]
    found.record("batch_visual", ready_for["visual_observation"]["status"])
    found.record("batch_visual_reason", ready_for["visual_observation"].get("reason"))
    found.record("batch_semantic", ready_for["semantic_observation"]["status"])
    found.record("batch_mutate", ready_for["mutate"]["status"])
    found.record("multi_pass", report["subsystems"]["perception"]["multi_pass"])
    found.record("state_domain", report["identity"]["state_domain"])
    found.record("world_resumption", report["identity"]["world_resumption"])

    found.expect(ready_for["visual_observation"]["status"] == "blocked",
                 "a batch-mode Unity claimed a usable capture path")
    found.expect(ready_for["semantic_observation"]["status"] == "ready",
                 "a missing SceneView was allowed to condemn the semantic read")
    found.expect(ready_for["mutate"]["status"] == "ready",
                 "a batch-mode Unity was told over the wire that it could not author")
    found.expect(report["subsystems"]["perception"]["multi_pass"] == "not_implemented",
                 "Unity claimed a multi-pass perception bundle it does not implement")
    found.expect(report["identity"]["state_domain"] == "authored",
                 "an edit-mode Unity did not report the authored domain")


def main() -> int:
    session = mcp_server._session("unity")
    try:
        report = public_flow.run(session, "unity",
                                 extra=batch_mode_is_reported_rather_than_hidden)
        # One connection for the whole flow, health included: a second socket
        # would describe a caller that no longer exists by the time anyone acted
        # on it, and would orphan whatever the first one was holding.
        report["findings"]["connection_generation"] = session.generation
        if session.generation != 1:
            report["failures"].append(
                f"[unity] health did not reuse the persistent session connection: "
                f"{session.generation}")
            report["ok"] = False
    except Exception as exc:  # noqa: BLE001 - the report is the whole output
        import traceback
        report = {"ok": False, "host": "unity",
                  "failures": [f"{type(exc).__name__}: {exc}"],
                  "findings": {"traceback": traceback.format_exc()}}
    finally:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass

    REPORT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
