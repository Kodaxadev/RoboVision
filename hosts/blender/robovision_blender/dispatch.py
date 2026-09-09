"""The protocol envelope: validate a request, run it, and shape what comes back.

Kept apart from the runtime because it is a different concern. The runtime owns
what the scene is; this owns what a caller is told, including the two things a
caller must never have to guess at: whether the state actually moved, and what
was recovered when an operation failed halfway.
"""
from __future__ import annotations

import time
import traceback
from typing import Any

import bpy

from .protocol import AUTHORED, PROTOCOL_VERSION
from .registry import AUTHORITATIVE, NOTIFIED, HostError

# A transaction's own bookkeeping is not a scene mutation, so it does not go
# through the accept path that decides `applied` versus `noop`.
_NOT_SCENE_MUTATIONS = {"transaction.begin", "transaction.commit"}


def validate_request(raw: dict[str, Any]) -> tuple[str, dict[str, Any], int | None]:
    if raw.get("rv") != PROTOCOL_VERSION:
        raise HostError("PROTOCOL_MISMATCH", f"expected protocol {PROTOCOL_VERSION}")
    if not isinstance(raw.get("id"), str) or not raw["id"]:
        raise HostError("INVALID_REQUEST", "id must be a non-empty string")
    method = raw.get("method")
    if not isinstance(method, str) or not method:
        raise HostError("INVALID_REQUEST", "method must be a non-empty string")
    params = raw.get("params", {})
    if not isinstance(params, dict):
        raise HostError("INVALID_REQUEST", "params must be an object")
    if_revision = raw.get("if_revision")
    if if_revision is not None and (
        not isinstance(if_revision, int) or isinstance(if_revision, bool) or if_revision < 0
    ):
        raise HostError("INVALID_REQUEST", "if_revision must be a non-negative integer")
    return method, params, if_revision


def _envelope(runtime, request_id: str, started: float, **extra: Any) -> dict[str, Any]:
    return {
        "rv": PROTOCOL_VERSION,
        "id": request_id,
        "revision": runtime.revision,
        "timing_ms": round((time.perf_counter() - started) * 1000.0, 3),
        **extra,
    }


def _error_payload(exc: HostError, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": exc.code, "message": str(exc), "retryable": exc.retryable}
    payload = exc.data if data is None else data
    if payload is not None:
        error["data"] = payload
    return error


def dispatch_request(runtime, raw: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    request_id = raw.get("id") if isinstance(raw.get("id"), str) else "invalid"
    mutation_before: dict[str, Any] | None = None
    try:
        method, params, if_revision = validate_request(raw)
        spec = runtime.registry.get(method)
        if spec.requires_ui and bpy.app.background:
            raise HostError("INVALID_CONTEXT", f"{method} requires an interactive Blender UI")

        # Read consistency is a property of the tool, decided here once, rather
        # than something each handler has to remember to arrange. A tool that
        # presents scene state re-reads first: otherwise it can return current
        # geometry stamped with the revision and journal position of the state
        # before it, which is worse than either being stale on its own.
        checkpoint: dict[str, Any] | None = None
        if spec.mutating or spec.reads == AUTHORITATIVE:
            # One full read serves both: the concurrency check below is only
            # meaningful against a revision that reflects the scene as it is
            # right now, and so is anything the handler is about to report.
            authoritative = runtime.resync()
            if spec.mutating:
                checkpoint = authoritative
        elif spec.reads == NOTIFIED:
            # Deliberately cheap. Service a notification if one arrived, but do
            # not pay for a deep read on every poll.
            runtime._refresh_dirty_state()
        if spec.mutating and if_revision is not None and if_revision != runtime.revision:
            raise HostError(
                "STALE_REVISION",
                "scene revision changed",
                data={"expected": if_revision, "actual": runtime.revision},
                retryable=True,
            )

        if checkpoint is not None and not method.startswith("transaction."):
            mutation_before = runtime.transactions.prepare_mutation(method, checkpoint)

        result = spec.handler(params, runtime)

        outcome: str | None = None
        if spec.mutating and method not in _NOT_SCENE_MUTATIONS:
            # `applied` and `noop` are both success; they differ in whether the
            # scene moved. A command that ran cleanly and left the state exactly
            # as it found it does not advance the revision, and the journal has
            # nothing to show for it either — the two must never disagree about
            # whether anything happened.
            moved = runtime._accept_own_mutation(before=checkpoint, request_id=request_id)
            outcome = "applied" if moved else "noop"

        # Blender has no runtime universe to confuse with the authored one, so
        # the domain is a constant here — reported anyway, because a client
        # driving both editors must be able to read the same field in both
        # rather than infer it from which host answered.
        response = _envelope(runtime, request_id, started, ok=True,
                             consistency=spec.reads, result=result,
                             state_domain=AUTHORED)
        if outcome is not None:
            response["outcome"] = outcome
        return response

    except HostError as exc:
        recovery_error, recovery = runtime._recover_failed_operation(mutation_before, exc, request_id)
        if recovery_error is not None:
            exc = recovery_error
        data = None
        if recovery is not None:
            data = {"operation_error_data": exc.data, "automatic_recovery": recovery}
        return _envelope(runtime, request_id, started, ok=False, error=_error_payload(exc, data))

    except Exception as exc:
        recovery_error, recovery = runtime._recover_failed_operation(mutation_before, exc, request_id)
        if recovery_error is not None:
            return _envelope(runtime, request_id, started, ok=False,
                             error=_error_payload(recovery_error))
        traceback.print_exc()
        error: dict[str, Any] = {
            "code": "HOST_EXCEPTION",
            "message": f"{type(exc).__name__}: host operation failed; see Blender console for traceback",
            "retryable": False,
        }
        if recovery is not None:
            error["data"] = {"automatic_recovery": recovery}
        return _envelope(runtime, request_id, started, ok=False, error=error)
