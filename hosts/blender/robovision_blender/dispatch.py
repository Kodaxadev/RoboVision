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

from . import undo
from .protocol import AUTHORED, HOST_VERSION, PROTOCOL_VERSION
from .idempotency import PROVED_NOT_APPLIED
from .recipe import CANONICAL_FRAME, coordinate_contract, recipe_hash, units
from .registry import AUTHORITATIVE, EXACT, NOTIFIED, REPLAY, UNKNOWN, HostError

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


def _envelope(runtime, request_id: str, started: float, *, consistency: str | None = None,
              **extra: Any) -> dict[str, Any]:
    """The response shape, which does not depend on whether the call succeeded.

    PROTOCOL.md said every response carries `state_domain` and `consistency`;
    measured, no error response on either host carried either of them. A failure
    still happened in a state domain, and — once a method resolves — still went
    through a tool with a declared consistency class. Before that point there is
    no class to report, and `unknown` says so rather than claiming the strongest
    one.
    """
    return {
        "rv": PROTOCOL_VERSION,
        "id": request_id,
        "revision": runtime.revision,
        "consistency": consistency or UNKNOWN,
        "state_domain": AUTHORED,
        "timing_ms": round((time.perf_counter() - started) * 1000.0, 3),
        **extra,
    }


def _error_payload(exc: HostError, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": exc.code, "message": str(exc), "retryable": exc.retryable}
    payload = exc.data if data is None else data
    if payload is not None:
        error["data"] = payload
    return error


def _seeds_for(spec, params: dict[str, Any]) -> dict[str, Any]:
    """Collect the randomness this operation will use, or refuse to guess it.

    The host must never pick a seed itself. If it did, and the reply were lost,
    the retry could not even describe the computation that may already have
    happened — the one thing a client needs in order to ask about it.
    """
    if not spec.seeds:
        return {}
    seeds: dict[str, Any] = {}
    missing = []
    for channel in spec.seeds:
        value = params.get(channel)
        if value is None:
            missing.append(channel)
        else:
            seeds[channel] = value
    if missing:
        raise HostError(
            "SEED_REQUIRED",
            f"{spec.name} is stochastic and needs its randomness recorded: {', '.join(missing)}",
            data={
                "method": spec.name,
                "missing_seeds": missing,
                "seed_channels": list(spec.seeds),
                "determinism": spec.determinism,
                "remedy": "Generate a seed once, keep it with the operation, and send it "
                          "with every retry.",
            },
        )
    return seeds


def _recipe_for(runtime, spec, params: dict[str, Any], seeds: dict[str, Any]) -> str:
    """What was asked for, independent of which delivery is asking."""
    hashable = {key: value for key, value in params.items() if key not in spec.seeds}
    return recipe_hash(
        method=spec.name,
        params=hashable,
        tool_version=HOST_VERSION,
        determinism=spec.determinism,
        seeds=seeds,
        targets={"world": runtime.world_incarnation},
    )


def _close_failed(runtime, intent, key, exc, recovery) -> None:
    """Close the record for an operation that did not succeed.

    Whether the key may be used again turns on evidence rather than on the fact
    of failure. If automatic recovery proved the pre-operation fingerprint was
    restored, the operation definitively did not apply, so the same key may be
    delivered again and execute — that is the useful outcome for a client
    retrying a failed call. Without that proof the honest answer is that nobody
    knows, and a retry is told so instead of being run a second time.
    """
    if intent is None:
        return
    recovered = bool((recovery or {}).get("recovered"))
    code = getattr(exc, "code", type(exc).__name__)
    try:
        runtime.ledger.result(
            int(intent["sequence"]),
            outcome=PROVED_NOT_APPLIED if recovered else "indeterminate",
            error_code=code,
            recovered=recovered,
            post_revision=runtime.revision,
        )
    except OSError as ledger_error:
        # A ledger that cannot be written is not a reason to swallow the
        # original error, which is what the caller is actually waiting for. It is
        # recorded, though: `system.health` reports it, because a durable record
        # with a hole in it is exactly what a recovering agent must not trust
        # silently.
        runtime.note_ledger_error("result", ledger_error)
    if key is None:
        return
    if recovered:
        runtime.invocations.prove_not_applied(key)
    else:
        runtime.invocations.release(key)


AUTONOMOUS = "autonomous"


def _assert_autonomous_contract(spec, raw: dict[str, Any], params: dict[str, Any],
                                if_revision: int | None) -> None:
    """What an unattended loop must supply before it is allowed to author anything.

    Low-level delivery stays permissive so an operator at a console can still
    poke the host, but an agent driving it for hours cannot be trusted to have
    remembered any of this by convention. The world matters most: a *first*
    delivery planned against world A must not execute in world B merely because
    it is technically not a retry, and nothing but an explicit expected world
    catches that.
    """
    if raw.get("contract") != AUTONOMOUS:
        return
    if not spec.mutating and not spec.observation_bound:
        return
    missing = []
    # Both kinds of operation are pinned to the world and the unit convention
    # they were planned in. A first delivery in the wrong world is exactly as
    # wrong as a retry there, and a scene whose units were reinterpreted is a
    # different scene however unchanged its revision looks.
    if not isinstance(raw.get("expected_world"), str) or not raw.get("expected_world"):
        missing.append("expected_world")
    if not isinstance(raw.get("expected_coordinate_contract"), str) \
            or not raw.get("expected_coordinate_contract"):
        missing.append("expected_coordinate_contract")
    if if_revision is None:
        missing.append("if_revision")
    if spec.mutating:
        # Only a mutation can be applied twice, so only a mutation needs the
        # identity that makes a redelivery recognisable.
        if not isinstance(raw.get("idempotency_key"), str) or not raw.get("idempotency_key"):
            missing.append("idempotency_key")
        attempt = raw.get("attempt")
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            missing.append("attempt")
    missing.extend(channel for channel in spec.seeds if params.get(channel) is None)
    if missing:
        raise HostError(
            "CONTRACT_VIOLATION",
            f"{spec.name} was invoked under the autonomous contract without: "
            f"{', '.join(missing)}",
            data={"contract": AUTONOMOUS, "missing": missing, "method": spec.name},
        )


def dispatch_request(runtime, raw: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    request_id = raw.get("id") if isinstance(raw.get("id"), str) else "invalid"
    mutation_before: dict[str, Any] | None = None
    # Visible to the failure paths below: a reservation must never be left
    # looking like an operation that is still running.
    intent: dict[str, Any] | None = None
    key: str | None = None
    # What the answer would have been worth, for a failure that happens after the
    # tool is known. Before that it stays unknown rather than being reported as
    # the strongest class.
    consistency: str | None = None
    try:
        method, params, if_revision = validate_request(raw)
        spec = runtime.registry.get(method)
        consistency = spec.reads

        # A retry that names the world it was planned against can never be
        # reinterpreted in a different one. Checked before anything else,
        # because every answer below would otherwise be about the wrong world.
        expected_world = raw.get("expected_world")
        if isinstance(expected_world, str) and expected_world != runtime.world_incarnation:
            raise HostError(
                "STALE_WORLD",
                "that request was planned against an editing context that is no longer open",
                data={"expected_world": expected_world,
                      "current_world_incarnation": runtime.world_incarnation},
                retryable=True,
            )

        _assert_autonomous_contract(spec, raw, params, if_revision)

        # The unit and axis convention is pinned the same way the world is, and
        # for the same reason: a human can change what a unit means between an
        # agent's observation and its mutation without moving either the world
        # incarnation or the scene revision.
        expected_contract = raw.get("expected_coordinate_contract")
        if isinstance(expected_contract, str) and expected_contract != coordinate_contract():
            raise HostError(
                "COORDINATE_CONTRACT_CHANGED",
                "the unit or axis convention changed since this operation was planned",
                data={"expected_coordinate_contract": expected_contract,
                      "current_coordinate_contract": coordinate_contract(),
                      "units": units()},
                retryable=True,
            )

        # Randomness and identity are settled before any side effect, so a lost
        # reply leaves a client able to describe exactly what it asked for.
        seeds = _seeds_for(spec, params)
        recipe = _recipe_for(runtime, spec, params, seeds) if spec.mutating else None
        key = raw.get("idempotency_key")
        key = key if isinstance(key, str) and key else None
        attempt = raw.get("attempt")
        attempt = int(attempt) if isinstance(attempt, int) and attempt > 0 else 1
        if spec.requires_ui and bpy.app.background:
            raise HostError("INVALID_CONTEXT", f"{method} requires an interactive Blender UI")

        # Read consistency is a property of the tool, decided here once, rather
        # than something each handler has to remember to arrange. A tool that
        # presents scene state re-reads first: otherwise it can return current
        # geometry stamped with the revision and journal position of the state
        # before it, which is worse than either being stale on its own.
        # Concurrency policy: a transaction belongs to the connection that
        # opened it. Another client's mutation would otherwise join that
        # transaction silently and be rolled back with it, so it is refused
        # rather than guessed at. Reads stay open to everyone, and transaction
        # control checks its own authority — an orphan is proved, not claimed.
        if spec.mutating and not method.startswith("transaction."):
            runtime.transactions.assert_mutation_allowed(runtime.current_client_id)

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
        # Is this delivery a duplicate? Asked after the authoritative read, so a
        # replay reports the revision of the world as it is now, and before any
        # checkpoint is taken, so a duplicate costs nothing.
        #
        # Only for tools that declared they resolve duplicates by replaying. A
        # tool whose policy is `terminal_state` answers from its own state
        # machine, and short-circuiting that here would make the declaration a
        # lie: a second rollback carrying the same key would be handed the first
        # one's stored result instead of being told the transaction is finished.
        replays = key is not None and spec.duplicate_policy == REPLAY
        if replays:
            replay = runtime.invocations.check(key, recipe, attempt, runtime.world_incarnation)
            if replay is not None:
                return _envelope(runtime, request_id, started, consistency=consistency,
                                 ok=True, **replay)

        # Checked for observation-bound operations too, and before the handler
        # runs, so `transaction.begin` cannot take its checkpoint from a scene
        # that moved after the caller planned against it.
        if (spec.mutating or spec.observation_bound) and if_revision is not None \
                and if_revision != runtime.revision:
            raise HostError(
                "STALE_REVISION",
                "scene revision changed",
                data={"expected": if_revision, "actual": runtime.revision},
                retryable=True,
            )

        # Durable before the side effect, because a crash between changing
        # Blender and recording that it changed is exactly the case one
        # post-operation entry cannot describe.
        intent = None
        if spec.mutating:
            intent = runtime.ledger.intent(
                request_id=request_id,
                idempotency_key=key,
                attempt=attempt,
                method=method,
                recipe_hash=recipe,
                params_hash=recipe_hash(method=method, params=params, tool_version=HOST_VERSION,
                                        determinism=spec.determinism),
                seeds=seeds,
                determinism=spec.determinism,
                frame=CANONICAL_FRAME,
                transaction=runtime.transactions.active.id if runtime.transactions.active else None,
                state_domain=AUTHORED,
                pre_revision=runtime.revision,
                pre_fingerprint=checkpoint["fingerprint"] if checkpoint else None,
                tool_version=HOST_VERSION,
            )
            if replays:
                runtime.invocations.reserve(
                    key, recipe, runtime.world_incarnation,
                    intent.get("transaction"), int(intent["sequence"]),
                )

        if checkpoint is not None and not method.startswith("transaction."):
            mutation_before = undo.prepare_mutation(method, checkpoint)

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
        response = _envelope(runtime, request_id, started, consistency=spec.reads,
                             ok=True, result=result)
        if outcome is not None:
            response["outcome"] = outcome

        if intent is not None:
            runtime.ledger.result(
                int(intent["sequence"]),
                outcome=outcome or "completed",
                post_revision=runtime.revision,
                post_fingerprint=runtime.current_snapshot()["fingerprint"],
                journal_cursor=runtime.journal.cursor(),
                response={"result": result, "outcome": outcome},
            )
            if replays:
                runtime.invocations.complete(
                    key,
                    {"result": result, "outcome": outcome},
                    original={
                        "request_id": request_id,
                        "world_incarnation": runtime.world_incarnation,
                        "recipe_hash": recipe,
                        "pre_revision": intent.get("pre_revision"),
                        "pre_fingerprint": intent.get("pre_fingerprint"),
                        "post_revision": runtime.revision,
                        "post_fingerprint": runtime.current_snapshot()["fingerprint"],
                        "outcome": outcome,
                        "transaction": intent.get("transaction"),
                    },
                )
        return response

    except HostError as exc:
        recovery_error, recovery = runtime._recover_failed_operation(mutation_before, exc, request_id)
        if recovery_error is not None:
            exc = recovery_error
        data = None
        if recovery is not None:
            data = {"operation_error_data": exc.data, "automatic_recovery": recovery}
        _close_failed(runtime, intent, key, exc, recovery)
        return _envelope(runtime, request_id, started, consistency=consistency,
                         ok=False, error=_error_payload(exc, data))

    except Exception as exc:
        recovery_error, recovery = runtime._recover_failed_operation(mutation_before, exc, request_id)
        # Closed before either return. This path used to leave early when
        # recovery itself failed, and the invocation stayed reserved for the life
        # of the bridge: every later delivery of that key was answered
        # IN_PROGRESS about an operation that had long since stopped running.
        _close_failed(runtime, intent, key, recovery_error or exc, recovery)
        if recovery_error is not None:
            return _envelope(runtime, request_id, started, consistency=consistency,
                             ok=False, error=_error_payload(recovery_error))
        traceback.print_exc()
        error: dict[str, Any] = {
            "code": "HOST_EXCEPTION",
            "message": f"{type(exc).__name__}: host operation failed; see Blender console for traceback",
            "retryable": False,
        }
        if recovery is not None:
            error["data"] = {"automatic_recovery": recovery}
        return _envelope(runtime, request_id, started, consistency=consistency,
                         ok=False, error=error)
