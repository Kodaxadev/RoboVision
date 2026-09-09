"""Blender's undo mechanics, separate from who is allowed to drive them.

Ownership and lifecycle decide *whether* a rollback may run; everything here
decides whether it *can*, and proves it by comparing deep fingerprints rather
than trusting that undo did the right thing. The two fail for unrelated reasons
— an unauthorised caller and an undo stack that cannot reach the checkpoint are
different problems with different answers — so they live in different modules.
"""
from __future__ import annotations

from typing import Any

import bpy

from .registry import HostError
from .snapshots import DEEP, scene_snapshot


def assert_safe(action: str) -> None:
    """Refuse to drive global undo from a mode where it means something else.

    `ed.undo` in Edit/Sculpt/Pose mode walks that mode's own undo steps, so a
    recovery loop can shred live edits or drop out of the mode entirely instead
    of restoring the object state RoboVision checkpointed. Reporting the
    situation is the honest outcome; guessing is not.
    """
    mode = bpy.context.mode
    if mode == "OBJECT":
        return
    raise HostError(
        "RECOVERY_UNSAFE",
        f"cannot {action} through global undo while Blender is in {mode}",
        data={
            "mode": mode,
            "required_mode": "OBJECT",
            "remedy": "Return Blender to Object Mode, then retry the rollback or recovery.",
        },
    )


def assert_functional() -> None:
    """Refuse to claim a rollback the undo stack cannot deliver.

    In background Blender `ed.undo_push` and `ed.undo` both poll true and the
    operators report FINISHED, but nothing is restored. A transaction then looks
    healthy right up until it has to roll something back, and the generic
    ROLLBACK_INCOMPLETE that follows reads like corruption rather than like an
    unsupported configuration.
    """
    if bpy.app.background:
        raise HostError(
            "UNDO_UNAVAILABLE",
            "Blender's undo stack does not restore state in background mode, "
            "so a verified rollback cannot be performed",
            data={"background": True, "remedy": "run an interactive Blender for verified transactions"},
        )


def push(message: str) -> None:
    if not bpy.context.preferences.edit.use_global_undo:
        raise HostError("UNSUPPORTED", "Blender Global Undo is disabled")
    if not bpy.ops.ed.undo_push.poll():
        raise HostError("INVALID_CONTEXT", "Blender cannot create an undo boundary in the current context")
    result = bpy.ops.ed.undo_push(message=message)
    if "FINISHED" not in result:
        raise HostError("HOST_EXCEPTION", "Blender failed to create an undo boundary")


def seal_current_state(message: str) -> bool:
    """Make un-pushed changes an undo step so the next undo lands on target.

    Blender's `ed.undo` steps to the state stored by the previous push, and
    property writes made from Python are not pushed on their own. Undoing
    straight after such a write therefore skips past the checkpoint instead of
    returning to it, which is how a "restored" scene could come back missing the
    object entirely. Sealing the live state first makes the first undo land
    exactly on the checkpoint.
    """
    try:
        push(message)
    except HostError:
        return False
    return True


def step_back_to(target: str, *, label: str, max_steps: int) -> tuple[bool, int, dict[str, Any], bool]:
    """Undo until the scene hashes to `target`, or until undo stops helping.

    Returns whether it arrived, how many steps it took, the last snapshot taken,
    and whether the live state had to be sealed first.
    """
    sealed = seal_current_state(label)
    current = scene_snapshot(level=DEEP)
    for step in range(1, max_steps + 1):
        if not bpy.ops.ed.undo.poll():
            break
        result = bpy.ops.ed.undo()
        if "FINISHED" not in result:
            break
        current = scene_snapshot(level=DEEP)
        if current["fingerprint"] == target:
            return True, step, current, sealed
    return False, 0, current, sealed


def prepare_mutation(label: str, before: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a pre-operation undo point and return a deep recovery checkpoint."""
    checkpoint = before if before is not None else scene_snapshot(level=DEEP)
    if checkpoint.get("level") != DEEP:
        raise HostError("HOST_EXCEPTION", "recovery checkpoints require a deep snapshot")
    push(f"RoboVision OP {label}")
    return checkpoint


def recover_failed_mutation(before: dict[str, Any], *, max_steps: int = 16) -> dict[str, Any]:
    """Restore the exact pre-operation fingerprint after a handler failure."""
    target = before["fingerprint"]
    current = scene_snapshot(level=DEEP)
    if current["fingerprint"] == target:
        # The operation failed its preconditions without touching state.
        return {"recovered": True, "undo_steps": 0, "fingerprint": target}

    assert_functional()
    assert_safe("recover")
    arrived, steps, current, sealed = step_back_to(
        target, label="RoboVision RECOVER", max_steps=max_steps
    )
    if arrived:
        return {"recovered": True, "undo_steps": steps, "fingerprint": target}

    raise HostError(
        "MUTATION_RECOVERY_INCOMPLETE",
        "failed operation changed Blender state and automatic recovery did not restore "
        "the pre-operation fingerprint",
        data={
            "expected_fingerprint": target,
            "actual_fingerprint": current["fingerprint"],
            "max_steps": max_steps,
            "sealed_before_undo": sealed,
        },
    )
