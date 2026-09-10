"""Whether it is presently safe and meaningful to observe, correct, mutate and verify.

Not a ping, and not a dashboard. A responding socket proves the socket responds;
a single green light would destroy exactly the distinctions the state model
exists to preserve. So this answers the Artist Loop's five questions
independently — can I observe, can I begin this planned correction, can I mutate,
can I verify, do I have a transaction to resolve — and each answer carries the
short machine-readable basis it was decided on.

Two rules keep it honest:

- **anything not proven is `unknown`.** Never `ready` by default, and never a
  subsystem's state inferred from an overall summary.
- **health observes; it does not perturb.** No test write, no render, no undo, no
  synthetic edit. The one authoritative act is the resync dispatch performs for
  every authoritative read, which is what makes the world and revision reported
  here belong to the state just read rather than to a stale baseline.

Independent failures stay independent. An uncertain journal degrades incremental
polling and says nothing about whether the scene can be read; an orphaned
transaction blocks a new correction while observation stays perfectly fine;
background Blender has no interactive viewport and can still author.

And the rule that decides each answer: **a capability is `ready` only when the
mechanisms its own contract requires are presently available.** A correction is
transactional by definition — observe, propose, observe again, then either commit
or roll back with proof — so an editor that cannot prove a rollback has not got a
degraded correction capability, it has one whose reject branch does not exist.
That is `blocked`. It is not a statement about the editor's health, and nothing
else in the report moves with it.
"""
from __future__ import annotations

from typing import Any

import bpy

from .context import find_view3d
from .protocol import AUTHORED, HOST_VERSION, PROTOCOL_VERSION
from .recipe import coordinate_contract, units
from .registry import HostError
from .transactions import ACTIVE, ORPHANED

# The smallest vocabulary that distinguishes reality. Five values on purpose:
# `unknown` and `not_applicable` are different claims, and collapsing either into
# `degraded` would report a guess as a measurement.
READY = "ready"
DEGRADED = "degraded"
BLOCKED = "blocked"
UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"

# Worst-first, for the optional overall summary. It summarises the entries below
# and nothing else: no caller may need to infer a subsystem from it.
_SEVERITY = {BLOCKED: 3, UNKNOWN: 2, DEGRADED: 1, READY: 0, NOT_APPLICABLE: 0}

# What a transaction situation is, named once so the host, the client library and
# the tests cannot drift into three vocabularies.
NONE = "none"
ACTIVE_OWNED = "active_owned_by_this_connection"
ACTIVE_FOREIGN = "active_foreign"
ORPHANED_ADOPTION_REQUIRED = "orphaned_adoption_required"
CONTAMINATED = "contaminated"
RECOVERY_UNCERTAIN = "recovery_uncertain"


def _entry(status: str, **facts: Any) -> dict[str, Any]:
    return {"status": status, **facts}


def _worst(*statuses: str) -> str:
    return max(statuses, key=lambda status: _SEVERITY.get(status, 2))


# --------------------------------------------------------------- transaction


def _transaction_situation(runtime) -> tuple[str, dict[str, Any] | None]:
    """Which of the transaction state machine's outcomes this connection is in.

    The state machine is reused rather than reinterpreted: `state()` is the same
    non-secret view `system.hello` publishes, and nothing here ever claims the
    caller possesses a recovery credential — the host cannot know that, and
    saying it could is the one lie that would make recovery unsafe.
    """
    state = runtime.transactions.state()
    if state is None:
        return NONE, None
    if state["state"] == ORPHANED:
        return ORPHANED_ADOPTION_REQUIRED, state
    if state["state"] != ACTIVE:
        # Blender's lifecycle has no recovery-uncertain outcome of its own — an
        # add-on reload takes the checkpoint with it and the transaction is
        # abandoned rather than left half-provable — but the situation is named
        # here so a cross-editor client reads one vocabulary.
        return RECOVERY_UNCERTAIN, state
    if state["owner_client"] != runtime.current_client_id:
        return ACTIVE_FOREIGN, state
    if state["contaminated"]:
        return CONTAMINATED, state
    return ACTIVE_OWNED, state


# ------------------------------------------------------------------ readiness


def _semantic_observation(snapshot: dict[str, Any]) -> dict[str, Any]:
    """This response exists, so the scene was read authoritatively to produce it.

    Not an inference. Dispatch performs the authoritative resync for every
    authoritative read before the handler runs, so the fingerprint quoted here is
    the one that read produced — a scene that could not be read would have failed
    the call rather than reached this line.
    """
    return _entry(READY, basis="authoritative_resync_succeeded",
                  fingerprint=snapshot["fingerprint"])


def _visual_observation() -> dict[str, Any]:
    """Whether the implemented perception path is structurally usable right now.

    Asked, never exercised. Executing a capture to make health green would make
    health a side effect, and a green light bought with a render is not evidence
    about the next render anyway.
    """
    if bpy.app.background:
        return _entry(BLOCKED, reason="background_mode",
                      detail="background Blender has no interactive VIEW_3D to render from")
    try:
        find_view3d()
    except HostError:
        return _entry(BLOCKED, reason="interactive_view_unavailable")
    return _entry(READY, basis="interactive_view3d_available")


def _verified_rollback() -> tuple[bool, str | None]:
    """Whether a rollback can be *proved*, not merely attempted.

    In background Blender `ed.undo_push` and `ed.undo` both poll true and report
    FINISHED while restoring nothing, so a transaction looks healthy right up
    until it has to undo something. That is a missing mechanism, not a slow one.
    """
    if not bpy.context.preferences.edit.use_global_undo:
        return False, "global_undo_disabled"
    if bpy.app.background:
        return False, "verified_rollback_unavailable"
    return True, None


def _begin_correction(situation: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Whether a new planned correction could be carried out at all.

    Ready needs all of it: the editing state is appropriate, the world is settled,
    the coordinate contract is known, the scene is readable, no existing
    transaction makes a begin impossible — and a rollback can be proved. When one
    of those is absent the blocking state is named exactly rather than reported as
    a generic refusal.

    The last of them is why this is `blocked` rather than `degraded` without it. A
    correction is transactional: observe, propose a candidate, observe again, then
    accept and commit *or* reject and roll back with proof. `transaction.begin`
    would still return successfully here and the checkpoint would be real — what
    is missing is the branch that makes proposing a candidate safe, and a
    capability with no reject branch is not a weaker version of this one.
    """
    if situation != NONE:
        return _entry(BLOCKED, reason="transaction_" + situation)
    verified, reason = _verified_rollback()
    if not verified:
        return _entry(BLOCKED, reason=reason,
                      verified_rollback=False,
                      detail="a correction must be able to be rejected and provably undone; "
                             "the begin itself would still succeed",
                      remedy="run an interactive Blender with Global Undo enabled")
    return _entry(READY, basis="scene_readable_and_no_transaction_open",
                  verified_rollback=True,
                  begin_fingerprint=snapshot["fingerprint"])


def _mutate(situation: str) -> dict[str, Any]:
    """Whether the authored mutation path is presently eligible to be attempted.

    Not a promise that any particular operation will succeed — only that nothing
    in the current state would refuse it before its own contract is consulted.
    """
    if situation in (ACTIVE_FOREIGN, ORPHANED_ADOPTION_REQUIRED, RECOVERY_UNCERTAIN):
        return _entry(BLOCKED, reason="transaction_" + situation)
    if not bpy.context.preferences.edit.use_global_undo:
        return _entry(BLOCKED, reason="global_undo_disabled",
                      detail="every mutation takes a pre-operation undo point")
    if bpy.context.mode != "OBJECT":
        # Global undo in Edit/Sculpt/Pose mode walks that mode's own steps, so a
        # failed mutation's automatic recovery is refused RECOVERY_UNSAFE. The
        # mutation may still run; what is missing is the safety net.
        return _entry(DEGRADED, reason="non_object_mode_recovery_unsafe",
                      editor_mode=bpy.context.mode)
    basis = "transaction_owned_by_this_connection" \
        if situation in (ACTIVE_OWNED, CONTAMINATED) else "no_transaction_open"
    return _entry(READY, basis=basis)


def _finish_or_recover(situation: str, state: dict[str, Any] | None,
                       finished: dict[str, Any] | None) -> dict[str, Any]:
    """The transaction situation, reported without granting any authority over it.

    Deliberately never says the caller can recover. Only the client library knows
    whether it still holds the credential, and a host that implied it did would be
    handing out exactly the reassurance a stranger needs.
    """
    if situation == NONE:
        entry = _entry(NOT_APPLICABLE, situation=NONE)
        if finished is not None:
            entry["recently_finished"] = finished
        return entry
    status = DEGRADED if situation in (CONTAMINATED, RECOVERY_UNCERTAIN) else READY
    return _entry(
        status,
        situation=situation,
        transaction=state,
        verified_rollback_available=bool(state and state.get("verified_rollback_available")),
        note="the host cannot know whether any caller still holds the recovery credential",
    )


# ----------------------------------------------------------------- subsystems


def _journal(runtime) -> dict[str, Any]:
    """Compact journal facts, and what an uncertain one actually costs.

    An uncertain journal means "take an authoritative observation", not "the
    editor is unhealthy". Those are different sentences, and an agent that read
    the second when the first was true would stop working for no reason.
    """
    state = runtime.journal.state()
    certain = bool(state["certain"])
    incremental = _entry(READY, basis="journal_certain") if certain else _entry(
        DEGRADED,
        reason="journal_uncertain",
        detail=state.get("uncertain_reason"),
        remedy="take an authoritative observation to restore knowledge",
    )
    return {
        "status": READY if certain else DEGRADED,
        "certain": certain,
        "epoch": state["epoch"],
        "journal_incarnation": state["journal_incarnation"],
        "cursor": state["cursor"],
        "uncertain_reason": state.get("uncertain_reason"),
        "incremental_changes": incremental,
    }


def _operations(runtime) -> dict[str, Any]:
    """Only the facts an autonomous recovery would act on.

    Not a retention policy, an index, a search or a replay API — and no probe
    record is written to test the filesystem, because a mutation whose intent
    cannot be durably recorded already fails before its side effect.
    """
    failure = runtime.last_ledger_error
    return {
        "status": DEGRADED if failure is not None else READY,
        "ledger_world": runtime.ledger.world,
        "ledger_matches_current_world": runtime.ledger.world == runtime.world_incarnation,
        # Blender cannot prove a reloaded add-on woke up in the same world, so it
        # never adopts a previous world's records rather than claiming it did.
        "resumed": False,
        "resumption": NOT_APPLICABLE,
        "interrupted_invocations": runtime.invocations.interrupted_count(),
        "last_ledger_io_error": failure,
    }


def _executor(runtime) -> dict[str, Any]:
    """What this very call proves, and nothing beyond it.

    No watchdog and no invented metric. A successful `system.health` is strong
    evidence that request delivery and editor-thread execution worked for this
    call, and that is the whole claim.
    """
    transport = runtime.transport
    return {
        "status": READY,
        "basis": "this_request_was_delivered_and_executed_on_the_editor_thread",
        "transport": {
            "kind": "tcp-jsonl",
            "listening": bool(transport is not None and transport.running),
            "port": transport.port if transport is not None else None,
        },
        # Blender's transport drains a bounded budget per timer tick and keeps no
        # depth metric. Manufacturing one to fill a field would be the opposite of
        # what this operation is for.
        "queue_depth": NOT_APPLICABLE,
    }


# --------------------------------------------------------------------- report


def report(runtime) -> dict[str, Any]:
    """The whole readiness report, built from state that was just read."""
    snapshot = runtime.current_snapshot()
    situation, state = _transaction_situation(runtime)
    finished = None
    if runtime.transactions.finished:
        finished = next(reversed(runtime.transactions.finished.values()))

    semantic = _semantic_observation(snapshot)
    visual = _visual_observation()
    visual_verify: dict[str, Any] = _entry(visual["status"], metrics="not_implemented")
    if visual["status"] == READY:
        visual_verify["basis"] = "follows_visual_observation"
    else:
        visual_verify["reason"] = visual.get("reason")

    ready_for = {
        "semantic_observation": semantic,
        "visual_observation": visual,
        "begin_correction": _begin_correction(situation, snapshot),
        "mutate": _mutate(situation),
        # Verification follows observation until Deterministic Asset Truth gives
        # it something of its own to prove. Saying so beats inventing a geometry
        # validator here and calling the invention evidence.
        "semantic_verify": _entry(semantic["status"], basis="follows_semantic_observation",
                                  validators="not_implemented"),
        "visual_verify": visual_verify,
        "finish_or_recover": _finish_or_recover(situation, state, finished),
    }

    return {
        "status": _worst(*(entry["status"] for entry in ready_for.values())),
        "status_note": "a summary of ready_for; every entry below is independent and "
                       "no subsystem may be inferred from this value",
        "identity": {
            "protocol": PROTOCOL_VERSION,
            "host": {"name": "blender", "implementation": "robovision_blender",
                     "version": HOST_VERSION},
            "editor": {
                "name": "Blender",
                "version": bpy.app.version_string,
                "version_tuple": list(bpy.app.version),
                "background": bool(bpy.app.background),
                "mode": bpy.context.mode,
            },
            "bridge": runtime.bridge,
            "world_incarnation": runtime.world_incarnation,
            "world_resumed": None,
            "world_resumption": NOT_APPLICABLE,
            "document": runtime.document,
            "revision": runtime.revision,
            "fingerprint": snapshot["fingerprint"],
            "state_domain": AUTHORED,
            "coordinate_contract": coordinate_contract(),
            "units": units(),
            "journal_cursor": runtime.journal.cursor(),
            "journal_epoch": runtime.journal.epoch,
            "journal_certain": runtime.journal.certain,
            "your_client": runtime.current_client_id,
        },
        "ready_for": ready_for,
        "subsystems": {
            "journal": _journal(runtime),
            "transaction": {
                "status": ready_for["finish_or_recover"]["status"],
                "situation": situation,
                "state": state,
            },
            "operations": _operations(runtime),
            "executor": _executor(runtime),
            "perception": {
                "status": visual["status"],
                "multi_pass": "implemented",
                "methods": ["viewport.capture", "perception.capture_bundle"],
                "requires_interactive_view": True,
            },
        },
    }
