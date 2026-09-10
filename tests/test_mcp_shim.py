"""The participant's wire. Six tools, and every failure is an answer.

The shim is the only thing between a frontier model in someone else's client and
the benchmark, so two properties matter more than the plumbing.

**The tool list is the boundary.** `restore` and `finalize` are operator
lifecycle: a participant able to restore could wipe its own failed attempts and
start over, and one able to finalize could pick the moment its numbers looked
best. Neither may ever appear here.

**Nothing kills the server.** A participant reaching for a mutating operation,
submitting a malformed correction, or spending a budget it has exhausted must be
told so and left able to continue. Each of those is evidence about the
participant; a crash would end the run instead of recording the reach.
"""
from __future__ import annotations

import json

import pytest

from benchmarks import mcp_shim
from benchmarks.facade import Denied
from robovision.errors import RoboVisionError


class Facade:
    """Answers the six calls, and can be told to fail in each realistic way."""

    def __init__(self, raises: BaseException | None = None):
        self.raises = raises
        self.seen: list[tuple] = []

    def _maybe(self, name, *args):
        self.seen.append((name, *args))
        if self.raises is not None:
            raise self.raises
        return {"ok": name}

    def health(self):
        return self._maybe("health")

    def observe(self):
        return self._maybe("observe")

    def call(self, method, params):
        return self._maybe("call", method, params)

    def schema(self, method):
        return self._maybe("schema", method)

    def submit_correction(self, correction):
        return self._maybe("submit_correction", correction)

    def stop(self, reason, note):
        return self._maybe("stop", reason, note)


def shim(raises=None):
    return mcp_shim.Shim(Facade(raises))


def body(result):
    return json.loads(result["content"][0]["text"])


def call(name, arguments=None, raises=None):
    server = shim(raises)
    result = mcp_shim.call_tool(server, {"name": name, "arguments": arguments or {}})
    return result, body(result), server.facade


# ------------------------------------------------------------------ the boundary

def test_exactly_six_tools_are_offered():
    assert [tool["name"] for tool in mcp_shim.TOOLS] == [
        "health", "observe", "call", "schema", "submit_correction", "stop"]


@pytest.mark.parametrize("forbidden", ["restore", "finalize", "verify"])
def test_operator_lifecycle_is_not_a_tool(forbidden):
    """The reason this matters is not tidiness.

    A participant that can restore can erase a failed attempt and try again
    against the same budget; one that can finalize can choose the moment its
    numbers look best. Both would make every reported figure unfalsifiable.
    """
    assert forbidden not in [tool["name"] for tool in mcp_shim.TOOLS]
    result, payload, facade = call(forbidden)
    assert result["isError"] is True
    assert "no such benchmark tool" in payload["error"]
    assert facade.seen == []


def test_the_correction_schema_requires_something_to_improve():
    schema = next(tool for tool in mcp_shim.TOOLS
                  if tool["name"] == "submit_correction")["inputSchema"]
    assert "targets" in schema["required"]
    assert "operations" in schema["required"]


def test_stop_offers_only_the_frozen_reasons():
    reasons = next(tool for tool in mcp_shim.TOOLS
                   if tool["name"] == "stop")["inputSchema"]["properties"]["reason"]
    assert set(reasons["enum"]) == set(mcp_shim.STOP_REASONS)


# ------------------------------------------------------------------- dispatch

def test_each_tool_reaches_its_facade_call():
    assert call("health")[2].seen == [("health",)]
    assert call("observe")[2].seen == [("observe",)]
    assert call("call", {"method": "scene.describe"})[2].seen == [
        ("call", "scene.describe", {})]
    assert call("schema", {"method": "object.transform"})[2].seen == [
        ("schema", "object.transform")]
    assert call("stop", {"reason": "evidence_insufficient"})[2].seen == [
        ("stop", "evidence_insufficient", "")]


def test_a_correction_is_passed_through_whole():
    correction = {"objective": "o", "reason": "r", "targets": [], "operations": []}
    _, _, facade = call("submit_correction", correction)
    assert facade.seen == [("submit_correction", correction)]


# --------------------------------------------------------- failures are answers

def test_a_denied_operation_is_an_answer_not_a_crash():
    result, payload, _ = call(
        "call", {"method": "object.transform"},
        raises=Denied("BENCHMARK_METHOD_DENIED", "the host declares this mutating"))
    assert result["isError"] is True
    assert "mutating" in payload["error"]


def test_a_refused_correction_reports_what_was_wrong_with_it():
    """The evaluator's refusal is the most useful message a participant gets."""
    result, payload, _ = call(
        "submit_correction", {"targets": []},
        raises=RoboVisionError("INVALID_PARAMS", "targets is required"))
    assert result["isError"] is True
    assert "targets is required" in payload["error"]


def test_an_exhausted_budget_does_not_exit_the_process():
    """`SystemExit` is how the runner ends an operator command. Not here."""
    result, payload, _ = call("submit_correction", {},
                              raises=SystemExit("BUDGET_EXHAUSTED 8 of 8"))
    assert result["isError"] is True
    assert "BUDGET_EXHAUSTED" in payload["error"]


def test_a_missing_argument_is_named():
    result, payload, _ = call("call", {})
    assert result["isError"] is True
    assert "method" in payload["error"]


def test_an_unexpected_failure_is_reported_rather_than_fatal():
    result, payload, _ = call("observe", raises=ZeroDivisionError("boom"))
    assert result["isError"] is True
    assert "ZeroDivisionError" in payload["error"]


# -------------------------------------------------------------------- protocol

def test_initialize_echoes_the_client_protocol_version():
    """A client on a different revision of the spec is not refused over a string."""
    response = mcp_shim.handle(shim(), {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"}})
    assert response["result"]["protocolVersion"] == "2024-11-05"
    assert response["result"]["capabilities"] == {"tools": {}}


def test_initialize_falls_back_when_the_client_names_none():
    response = mcp_shim.handle(shim(), {"jsonrpc": "2.0", "id": 1,
                                        "method": "initialize", "params": {}})
    assert response["result"]["protocolVersion"] == mcp_shim.PROTOCOL


def test_notifications_get_no_reply():
    assert mcp_shim.handle(shim(), {"jsonrpc": "2.0",
                                    "method": "notifications/initialized"}) is None


def test_an_unknown_method_with_an_id_gets_an_error_and_without_one_gets_silence():
    assert mcp_shim.handle(shim(), {"jsonrpc": "2.0", "id": 3,
                                    "method": "nope"})["error"]["code"] == -32601
    assert mcp_shim.handle(shim(), {"jsonrpc": "2.0", "method": "nope"}) is None


def test_tools_list_is_served():
    listed = mcp_shim.handle(shim(), {"jsonrpc": "2.0", "id": 2,
                                      "method": "tools/list"})["result"]["tools"]
    assert len(listed) == 6
    assert all("inputSchema" in tool and tool["description"] for tool in listed)
