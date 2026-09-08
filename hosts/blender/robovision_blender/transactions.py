from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import bpy

from .registry import HostError
from .snapshots import DEEP, scene_snapshot


@dataclass(slots=True)
class Transaction:
    id: str
    label: str
    begin_snapshot: dict[str, Any]
    external_change_fingerprint: str | None = None


class TransactionManager:
    def __init__(self) -> None:
        self.active: Transaction | None = None

    @staticmethod
    def _assert_undo_is_safe(action: str) -> None:
        """Refuse to drive global undo from a mode where it means something else.

        `ed.undo` in Edit/Sculpt/Pose mode walks that mode's own undo steps, so a
        recovery loop can shred live edits or drop out of the mode entirely
        instead of restoring the object state RoboVision checkpointed. Reporting
        the situation is the honest outcome; guessing is not.
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

    @classmethod
    def _seal_current_state(cls, message: str) -> bool:
        """Make un-pushed changes an undo step so the next undo lands on target.

        Blender's `ed.undo` steps to the state stored by the previous push, and
        property writes made from Python are not pushed on their own. Undoing
        straight after such a write therefore skips past the checkpoint instead
        of returning to it, which is how a "restored" scene could come back
        missing the object entirely. Sealing the live state first makes the
        first undo land exactly on the checkpoint.
        """
        try:
            cls._push_undo(message)
        except HostError:
            return False
        return True

    @staticmethod
    def _push_undo(message: str) -> None:
        if not bpy.context.preferences.edit.use_global_undo:
            raise HostError("UNSUPPORTED", "Blender Global Undo is disabled")
        if not bpy.ops.ed.undo_push.poll():
            raise HostError("INVALID_CONTEXT", "Blender cannot create an undo boundary in the current context")
        result = bpy.ops.ed.undo_push(message=message)
        if "FINISHED" not in result:
            raise HostError("HOST_EXCEPTION", "Blender failed to create an undo boundary")

    def begin(self, tx_id: str, label: str, before: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.active is not None:
            raise HostError("TRANSACTION_ACTIVE", "a transaction is already active", data={"transaction": self.active.id})
        # The caller passes the runtime's freshly resynced snapshot so the
        # baseline and the runtime's revision tracking describe the same state.
        # Taking an independent snapshot here could leave the runtime believing
        # the scene changed between begin and the transaction's first mutation.
        if before is None or before.get("level") != DEEP:
            before = scene_snapshot(level=DEEP)
        self._push_undo(f"RoboVision BEGIN {label}")
        self.active = Transaction(tx_id, label, before)
        return {
            "transaction": tx_id,
            "label": label,
            "begin_fingerprint": before["fingerprint"],
            "contaminated": False,
        }

    def mark_external_change(self, fingerprint: str) -> None:
        tx = self.active
        if tx is None or fingerprint == tx.begin_snapshot["fingerprint"]:
            return
        if tx.external_change_fingerprint is None:
            tx.external_change_fingerprint = fingerprint

    def prepare_mutation(self, label: str, before: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create a pre-operation undo point and return a deep recovery checkpoint."""
        checkpoint = before if before is not None else scene_snapshot(level=DEEP)
        if checkpoint.get("level") != DEEP:
            raise HostError("HOST_EXCEPTION", "recovery checkpoints require a deep snapshot")
        self._push_undo(f"RoboVision OP {label}")
        return checkpoint

    def recover_failed_mutation(self, before: dict[str, Any], *, max_steps: int = 16) -> dict[str, Any]:
        """Restore the exact pre-operation fingerprint after a handler failure."""
        target = before["fingerprint"]
        current = scene_snapshot(level=DEEP)
        if current["fingerprint"] == target:
            # The operation failed its preconditions without touching state.
            return {"recovered": True, "undo_steps": 0, "fingerprint": target}

        self._assert_undo_is_safe("recover")
        sealed = self._seal_current_state("RoboVision RECOVER")
        for step in range(1, max_steps + 1):
            if not bpy.ops.ed.undo.poll():
                break
            result = bpy.ops.ed.undo()
            if "FINISHED" not in result:
                break
            current = scene_snapshot(level=DEEP)
            if current["fingerprint"] == target:
                return {"recovered": True, "undo_steps": step, "fingerprint": target}

        raise HostError(
            "MUTATION_RECOVERY_INCOMPLETE",
            "failed operation changed Blender state and automatic recovery did not restore the pre-operation fingerprint",
            data={
                "expected_fingerprint": target,
                "actual_fingerprint": current["fingerprint"],
                "max_steps": max_steps,
                "sealed_before_undo": sealed,
            },
        )

    def commit(self, tx_id: str, *, force: bool = False) -> dict[str, Any]:
        tx = self._require(tx_id)
        self._assert_safe_or_forced(tx, force=force, action="commit")
        self._push_undo(f"RoboVision COMMIT {tx.label}")
        after = scene_snapshot(level=DEEP)
        self.active = None
        return {
            "transaction": tx.id,
            "committed": True,
            "begin_fingerprint": tx.begin_snapshot["fingerprint"],
            "final_fingerprint": after["fingerprint"],
            "forced_after_external_change": tx.external_change_fingerprint is not None,
        }

    def rollback(self, tx_id: str, *, max_steps: int = 128, force: bool = False) -> dict[str, Any]:
        tx = self._require(tx_id)
        self._assert_safe_or_forced(tx, force=force, action="rollback")
        target = tx.begin_snapshot["fingerprint"]
        current = scene_snapshot(level=DEEP)
        if current["fingerprint"] != target:
            self._assert_undo_is_safe("roll back")
        if current["fingerprint"] == target:
            self.active = None
            return {
                "transaction": tx.id,
                "rolled_back": True,
                "undo_steps": 0,
                "fingerprint": target,
                "forced_after_external_change": tx.external_change_fingerprint is not None,
            }

        self._seal_current_state(f"RoboVision ROLLBACK {tx.label}")
        for step in range(1, max_steps + 1):
            if not bpy.ops.ed.undo.poll():
                break
            result = bpy.ops.ed.undo()
            if "FINISHED" not in result:
                break
            current = scene_snapshot(level=DEEP)
            if current["fingerprint"] == target:
                self.active = None
                return {
                    "transaction": tx.id,
                    "rolled_back": True,
                    "undo_steps": step,
                    "fingerprint": target,
                    "forced_after_external_change": tx.external_change_fingerprint is not None,
                }

        raise HostError(
            "ROLLBACK_INCOMPLETE",
            "undo did not restore the transaction begin fingerprint",
            data={
                "transaction": tx.id,
                "expected_fingerprint": target,
                "actual_fingerprint": current["fingerprint"],
                "max_steps": max_steps,
            },
        )

    @staticmethod
    def _assert_safe_or_forced(tx: Transaction, *, force: bool, action: str) -> None:
        if tx.external_change_fingerprint is None or force:
            return
        raise HostError(
            "TRANSACTION_CONTAMINATED",
            f"an out-of-band Blender edit occurred during the RoboVision transaction; refusing automatic {action}",
            data={
                "transaction": tx.id,
                "begin_fingerprint": tx.begin_snapshot["fingerprint"],
                "external_change_fingerprint": tx.external_change_fingerprint,
                "force_parameter": "Set force=true only if overwriting/including the external edit is intentional.",
            },
        )

    def _require(self, tx_id: str) -> Transaction:
        if self.active is None:
            raise HostError("NO_TRANSACTION", "no transaction is active")
        if tx_id != self.active.id:
            raise HostError("INVALID_PARAMS", "transaction id does not match active transaction", data={"active": self.active.id})
        return self.active
