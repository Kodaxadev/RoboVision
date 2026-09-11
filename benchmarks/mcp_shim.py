"""A stdio MCP server exposing exactly the participant's six tools.

The participant is a frontier model in some other client, and it needs to reach
`BenchmarkFacade` over a wire rather than as a Python object. This is that wire
and nothing more: it translates MCP tool calls into facade calls, and it adds no
capability the facade does not already grant.

What it deliberately does **not** expose is as important as what it does.
`restore` and `finalize` are operator lifecycle — a participant able to restore
could wipe its own failed attempts and start again, and one able to finalize
could pick the moment its numbers looked best. Both stay on the operator's side
of the boundary, run from the operator's own terminal.

Every refusal the facade makes arrives here as a tool error rather than a crash,
because a participant that reaches for a mutating operation should be told no and
allowed to carry on — the refusal is data about the participant, and killing the
server would destroy the run instead of recording the reach.

Written against the MCP wire format directly rather than through an SDK. The
surface used is small — initialize, tools/list, tools/call — and a benchmark
harness that could fail because a transport dependency changed under it is a
worse trade than sixty lines of JSON-RPC. Logs go to stderr; stdout carries the
protocol and nothing else, so a stray print would corrupt the session.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from benchmarks.facade import BenchmarkFacade, Denied  # noqa: E402
from benchmarks.runner import STOP_REASONS, Runner  # noqa: E402
from robovision.errors import RoboVisionError  # noqa: E402
from robovision.session import HostSession  # noqa: E402

PROTOCOL = "2025-06-18"
SERVER = {"name": "rvbench", "version": "2"}

CORRECTION_SCHEMA = {
    "type": "object",
    "required": ["objective", "reason", "targets", "operations"],
    "properties": {
        "objective": {"type": "string",
                      "description": "your own words; recorded, never parsed"},
        "reason": {"type": "string",
                   "description": "what in the evidence led you here"},
        "targets": {
            "type": "array",
            "description": "metrics you claim will improve, each by at least its "
                           "epsilon. A correction with no targets cannot be "
                           "judged to have succeeded and is refused.",
            "items": {"type": "object", "required": ["metric", "epsilon"],
                      "properties": {"metric": {"type": "string"},
                                     "kind": {"type": "string"},
                                     "epsilon": {"type": "number"}}},
        },
        "protected": {
            "type": "array",
            "description": "metrics you claim will not worsen by more than tolerance",
            "items": {"type": "object", "required": ["metric", "tolerance"],
                      "properties": {"metric": {"type": "string"},
                                     "kind": {"type": "string"},
                                     "tolerance": {"type": "number"}}},
        },
        "locality": {
            "type": "object",
            "description": "what you intend to touch; anything changing outside "
                           "it fails the attempt",
            "properties": {"targets": {"type": "array", "items": {"type": "string"}},
                           "protected": {"type": "array", "items": {"type": "string"}},
                           "allowed": {"type": "array", "items": {"type": "string"}}},
        },
        "operations": {
            "type": "array",
            "description": "typed RoboVision calls, executed in order in one "
                           "transaction, each individually pinned",
            "items": {"type": "object", "required": ["method"],
                      "properties": {"method": {"type": "string"},
                                     "params": {"type": "object"}}},
        },
        "advisory": {"type": "array", "items": {"type": "string"},
                     "description": "metrics to record but not gate on"},
    },
}

TOOLS = [
    {
        "name": "health",
        "description": "The host's structured readiness report. Read it before "
                       "assuming a correction can be carried out.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "observe",
        "description": "The discrepancy packet: hard invariants, dimensions, "
                       "coverage, per-view reference agreement, pattern "
                       "conformance, the certificates block naming each metric's "
                       "kind, and an explicit limits block saying what is NOT "
                       "measured. This is your primary evidence. Free.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "call",
        "description": "Any read-only RoboVision operation: scene.describe, "
                       "scene.snapshot, scene.raycast, object.inspect, "
                       "mesh.inspect, mesh.query, truth.* (geometry, spatial, "
                       "coverage, reference, pattern, views, silhouette, compare, "
                       "measure, locality, evaluate), viewport.focus / "
                       "viewport.axis / viewport.capture for shaded pictures, and "
                       "system.*. Mutating operations and transaction control are "
                       "refused: everything that changes the asset goes through "
                       "submit_correction. Observation is unbudgeted.",
        "inputSchema": {
            "type": "object", "required": ["method"],
            "properties": {"method": {"type": "string"},
                           "params": {"type": "object"}},
        },
    },
    {
        "name": "schema",
        "description": "The exact parameter schema of ANY operation, including "
                       "mutating ones. Reading a mutation's schema is how a "
                       "correction gets written; it is not performing one.",
        "inputSchema": {"type": "object", "required": ["method"],
                        "properties": {"method": {"type": "string"}}},
    },
    {
        "name": "submit_correction",
        "description": "The only way anything changes. Consumes one attempt from "
                       "the budget. Opens a transaction, delivers your typed "
                       "operations under strict pins, measures again, evaluates, "
                       "then commits or rolls back to the exact pre-attempt "
                       "state. Returns the decision, the targets achieved, the "
                       "epsilon audit and refreshed evidence.",
        "inputSchema": CORRECTION_SCHEMA,
    },
    {
        "name": "stop",
        "description": "End the run deliberately and say why. Stopping is a "
                       "result, not a forfeit: an honest 'the evidence does not "
                       "localise this' is more informative than spending the "
                       "budget guessing.",
        "inputSchema": {
            "type": "object", "required": ["reason"],
            "properties": {
                "reason": {"type": "string", "enum": sorted(STOP_REASONS),
                           "description": "; ".join(f"{k}: {v}" for k, v
                                                    in sorted(STOP_REASONS.items()))},
                "note": {"type": "string",
                         "description": "your own words about why you stopped"},
            },
        },
    },
]


def log(message: str) -> None:
    """stderr only. stdout is the protocol."""
    print(message, file=sys.stderr, flush=True)


class Shim:
    def __init__(self, facade: BenchmarkFacade) -> None:
        self.facade = facade

    def dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "health":
            return self.facade.health()
        if name == "observe":
            return self.facade.observe()
        if name == "call":
            return self.facade.call(arguments["method"], arguments.get("params") or {})
        if name == "schema":
            return self.facade.schema(arguments["method"])
        if name == "submit_correction":
            return self.facade.submit_correction(arguments)
        if name == "stop":
            return self.facade.stop(arguments["reason"], arguments.get("note", ""))
        raise Denied("BENCHMARK_UNKNOWN_TOOL",
                     f"no such benchmark tool: {name}",
                     data={"available": [tool["name"] for tool in TOOLS]})


def text(payload: Any) -> dict[str, Any]:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, indent=2, default=str)}]}


def failure(message: str, data: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {"error": message}
    if data is not None:
        body["data"] = data
    return {"isError": True,
            "content": [{"type": "text",
                         "text": json.dumps(body, indent=2, default=str)}]}


def call_tool(shim: Shim, params: dict[str, Any]) -> dict[str, Any]:
    """Every failure becomes a tool error, never a dead server.

    A participant reaching for a mutating operation, submitting a malformed
    correction, or trying to spend a budget it has exhausted must be told so and
    left able to continue. Each of those is evidence about the participant, and
    the run is the thing being measured.
    """
    name = params.get("name", "")
    arguments = params.get("arguments") or {}
    try:
        return text(shim.dispatch(name, arguments))
    except Denied as exc:
        return failure(str(exc), exc.payload.data)
    except RoboVisionError as exc:
        # Includes a correction the evaluator refused. The harness has already
        # recorded the attempt and put the scene back; the participant is told
        # what was wrong with it.
        return failure(f"{exc.payload.code}: {exc}", exc.payload.data)
    except SystemExit as exc:
        # The runner ends the process for budget exhaustion and for a run that
        # has already stopped. Here those are answers, not exits.
        return failure(str(exc))
    except KeyError as exc:
        return failure(f"missing required argument: {exc}")
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        log(traceback.format_exc())
        return failure(f"{type(exc).__name__}: {exc}")


def handle(shim: Shim, request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        requested = (request.get("params") or {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0", "id": request_id,
            "result": {
                # Echoed when the client named one, so a client on a different
                # revision of the spec is not refused over a version string.
                "protocolVersion": requested if isinstance(requested, str) else PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER,
            },
        }
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": call_tool(shim, request.get("params") or {})}
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def serve(shim: Shim) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            log(f"ignoring unparseable line: {line[:200]}")
            continue
        response = handle(shim, request)
        if response is not None:
            sys.stdout.write(json.dumps(response, default=str) + "\n")
            sys.stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    session = HostSession(args.host, args.port)
    runner = Runner(session, args.benchmark, args.model)
    # Refuses to serve an unrestored run rather than letting a participant
    # measure whatever happened to be in the editor. `restore` is the operator's
    # job and is deliberately not a tool.
    verification = runner.root / "restore-verification.json"
    if not verification.is_file():
        log(f"no restore verification at {verification}; run "
            f"`python benchmarks/runner.py restore {args.benchmark} {args.model}` "
            f"before starting the participant")
        return 2
    report = json.loads(verification.read_text(encoding="utf-8"))
    if not report.get("equivalent") or report.get("q0_matches_frozen") is False:
        log(f"the restored asset does not match the frozen benchmark: {report}")
        return 2

    # A finished run is never served. On 2026-09-10 a desktop client kept an
    # old shim alive for a completed run and routed a new participant to it;
    # that participant then rewrote the finished run's stop record. A shim for
    # a stopped or finalized run now refuses to start at all.
    finished = [name for name in ("stop.json", "report.json")
                if (runner.root / name).is_file()]
    if finished:
        log(f"run {args.model} is finished ({', '.join(finished)} present); "
            f"refusing to serve it. A new experiment needs a new run identity.")
        return 2
    try:
        bound = runner.bind()
    except SystemExit as exc:
        log(str(exc))
        return 2

    log(f"rvbench shim ready: {args.benchmark} / {args.model} "
        f"[pid {os.getpid()}, scene bound by {bound['kind']}] on "
        f"{args.host}:{args.port}; tools="
        f"{', '.join(tool['name'] for tool in TOOLS)}")
    try:
        return serve(Shim(BenchmarkFacade(session, runner)))
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
