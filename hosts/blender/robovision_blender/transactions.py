from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import bpy

from .registry import HostError
from .snapshots import scene_snapshot


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
    def _push_undo(message: str) -> None:
        if not bpy.context.preferences.edit.use_global_undo:
            raise HostError("UNSUPPORTED", "Blender Global Undo is disabled")
        if not bpy.ops.ed.undo_push.poll():
            raise HostError("INVALID_CONTEXT", "Blender cannot create an undo boundary in the current context")
        result = bpy.ops.ed.undo_push(message=message)
        if "FINISHED" not in result:
            raise HostError("HOST_EXCEPTION", "Blender failed to create an undo boundary")

    def begin(self, tx_id: str, label: str) -> dict[str, Any]:
        if self.active is not None:
            raise HostError("TRANSACTION_ACTIVE", "a transaction is already active", data={"transaction": self.active.id})
        before = scene_snapshot(deep=True)
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

    def prepare_standalone_mutation(self, label: str) -> None:
        if self.active is None:
            self._push_undo(f"RoboVision {label}")

    def commit(self, tx_id: str, *, force: bool = False) -> dict[str, Any]:
        tx = self._require(tx_id)
        self._assert_safe_or_forced(tx, force=force, action="commit")
        self._push_undo(f"RoboVision COMMIT {tx.label}")
        after = scene_snapshot(deep=True)
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
        current = scene_snapshot(deep=True)
        if current["fingerprint"] == target:
            self.active = None
            return {
                "transaction": tx.id,
                "rolled_back": True,
                "undo_steps": 0,
                "fingerprint": target,
                "forced_after_external_change": tx.external_change_fingerprint is not None,
            }

        for step in range(1, max_steps + 1):
            if not bpy.ops.ed.undo.poll():
                break
            bpy.ops.ed.undo()
            current = scene_snapshot(deep=True)
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
