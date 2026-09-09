"""Who holds a transaction, which world it belongs to, and what it may still do.

A transaction used to be an id, a label and a begin snapshot, owned by nobody in
particular: any connection could mutate inside another's transaction, and any
connection could finish one. Ownership is now the connection that opened it, and
authority to reclaim it is a secret handed out once — because a dropped TCP
connection is not authorization.

Lifecycle is an explicit state with a reason rather than a pair of booleans, and
identity is scoped to the world the transaction was opened in, so one from a
document that is no longer loaded is recognisably not this one.

The undo mechanics live in `undo.py`: ownership decides whether a rollback may
run, undo decides whether it can.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any
import uuid

import bpy

from . import undo
from .recovery import RecoveryToken
from .registry import HostError
from .snapshots import DEEP, scene_snapshot

ACTIVE = "active"
ORPHANED = "orphaned"
ABANDONED = "abandoned"
COMMITTED = "committed"
ROLLED_BACK = "rolled_back"


@dataclass(slots=True)
class Transaction:
    id: str
    label: str
    world: str
    begin_revision: int
    begin_snapshot: dict[str, Any]
    owner_client: int
    recovery: RecoveryToken
    state: str = ACTIVE
    reason: str | None = None
    external_change_fingerprint: str | None = None


class TransactionManager:
    def __init__(self) -> None:
        self.active: Transaction | None = None
        # What happened to transactions that ended. Remembering the id lets a
        # later commit or rollback say what actually happened instead of "no
        # transaction is active", which reads like the client invented the id.
        self.finished: OrderedDict[str, dict[str, Any]] = OrderedDict()

    # ------------------------------------------------------------- lifecycle

    def abandon(self, *, reason: str) -> str | None:
        """Drop the active transaction because the world it described is gone.

        Rolling back is not an option: the begin fingerprint describes a
        document that is no longer open, so undo would either fail or operate on
        unrelated state. The honest outcome is to end the transaction and say
        why.
        """
        tx = self.active
        if tx is None:
            return None
        self._finish(tx, ABANDONED, reason)
        return tx.id

    def client_disconnected(self, client_id: int) -> None:
        """The owner's connection went away; the transaction waits for proof."""
        tx = self.active
        if tx is None or tx.state != ACTIVE or tx.owner_client != client_id:
            return
        tx.state = ORPHANED
        tx.reason = "owner_disconnected"

    def _finish(self, tx: Transaction, state: str, reason: str | None) -> None:
        record: dict[str, Any] = {"transaction": tx.id, "state": state, "world_incarnation": tx.world}
        if reason is not None:
            record["reason"] = reason
        self.finished[tx.id] = record
        while len(self.finished) > 32:
            self.finished.popitem(last=False)
        self.active = None

    def state(self) -> dict[str, Any] | None:
        """What a client may know about the transaction, and nothing more.

        Without the recovery token or its verifier. This is what `system.hello`
        reports and what health will consume later: it says who holds the
        transaction and whether a verified rollback is available, and never
        grants the authority to use one.
        """
        tx = self.active
        if tx is None:
            return None
        return {
            "transaction": tx.id,
            "label": tx.label,
            "state": tx.state,
            "world_incarnation": tx.world,
            "owner_client": tx.owner_client,
            "owner_disconnected": tx.state == ORPHANED,
            "begin_revision": tx.begin_revision,
            "begin_fingerprint": tx.begin_snapshot["fingerprint"],
            "contaminated": tx.external_change_fingerprint is not None,
            "adoption_required": tx.state == ORPHANED,
            "verified_rollback_available": not bpy.app.background,
            **({"reason": tx.reason} if tx.reason else {}),
        }

    # ----------------------------------------------------------------- begin

    def begin(self, label: str, *, world: str, revision: int, owner_client: int,
              before: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.active is not None:
            raise HostError("TRANSACTION_ACTIVE", "a transaction is already active",
                            data={"active": self.state()})
        # The caller passes the runtime's freshly resynced snapshot so the
        # baseline and the runtime's revision tracking describe the same state.
        # Taking an independent snapshot here could leave the runtime believing
        # the scene changed between begin and the transaction's first mutation.
        if before is None or before.get("level") != DEEP:
            before = scene_snapshot(level=DEEP)
        undo.push(f"RoboVision BEGIN {label}")
        token, secret = RecoveryToken.mint()
        self.active = Transaction(
            # Self-scoping: the world is in the id, so a transaction from a
            # document that is no longer loaded is recognisably not this one,
            # and a client's own label can never be mistaken for the host's
            # identity for it.
            id=f"rvtx:{world.partition(':')[2][:8]}:{uuid.uuid4().hex}",
            label=label,
            world=world,
            begin_revision=revision,
            begin_snapshot=before,
            owner_client=owner_client,
            recovery=token,
        )
        return {
            **self.state(),
            "begin_fingerprint": before["fingerprint"],
            # Stated up front rather than discovered at rollback time.
            "verified_rollback": not bpy.app.background,
            # Returned exactly once. No call gives it back.
            "recovery_token": secret,
            "recovery_token_note": (
                "Store this. It is the only way to reclaim this transaction after a disconnect, "
                "and the host keeps only a hash of it."
            ),
        }

    def mark_external_change(self, fingerprint: str) -> None:
        tx = self.active
        if tx is None or fingerprint == tx.begin_snapshot["fingerprint"]:
            return
        if tx.external_change_fingerprint is None:
            tx.external_change_fingerprint = fingerprint

    # ----------------------------------------------------------------- adopt

    def adopt(self, tx_id: str | None, offered: str | None, *, client_id: int) -> dict[str, Any]:
        """Take ownership of an orphaned transaction by proving you opened it.

        Every refusal leaves everything exactly as it was — owner, state,
        verifier, scene, revision and journal — because an adoption attempt that
        changes something is a way to attack a transaction without ever passing
        the check. The token is rotated on success so a leaked one cannot be
        replayed, and the replacement goes only to the client that proved it.
        """
        if not tx_id or not offered:
            raise self._refused("transaction and recovery_token are required")
        tx = self.active
        if tx is None or tx.id != tx_id:
            raise self._refused("no such transaction is open in this world")
        if tx.state != ORPHANED:
            raise self._refused("that transaction is not waiting to be adopted")
        if not tx.recovery.matches(offered):
            raise self._refused("the recovery token does not prove ownership of that transaction")

        tx.owner_client = client_id
        tx.state = ACTIVE
        tx.reason = None
        tx.recovery, rotated = RecoveryToken.mint()
        return {
            **self.state(),
            "adopted": True,
            "recovery_token": rotated,
            "recovery_token_note": "Rotated by this adoption; the previous token no longer works.",
        }

    @staticmethod
    def _refused(message: str) -> HostError:
        """One refusal for every failed adoption, saying nothing a guesser could use."""
        return HostError("TRANSACTION_ADOPTION_REFUSED", message, data={"adopted": False})

    def discard(self, tx_id: str | None, *, client_id: int) -> dict[str, Any]:
        """Give up a transaction rather than claim an outcome for it."""
        tx = self._locate(tx_id)
        # An owned, healthy transaction is its owner's to discard. An orphaned
        # one has no owner left to ask.
        if tx.state == ACTIVE and tx.owner_client != client_id:
            raise self._foreign(tx, client_id)
        was = tx.state
        reason = tx.reason or "discarded_by_client"
        self._finish(tx, ABANDONED, reason)
        return {"transaction": tx.id, "state": ABANDONED, "discarded_from": was, "reason": reason}

    # ---------------------------------------------------- commit / rollback

    def commit(self, tx_id: str | None, *, client_id: int, force: bool = False) -> dict[str, Any]:
        tx = self._require(tx_id, client_id)
        self._assert_safe_or_forced(tx, force=force, action="commit")
        undo.push(f"RoboVision COMMIT {tx.label}")
        after = scene_snapshot(level=DEEP)
        contaminated = tx.external_change_fingerprint is not None
        self._finish(tx, COMMITTED, None)
        return {
            "transaction": tx.id,
            "committed": True,
            "state": COMMITTED,
            "begin_fingerprint": tx.begin_snapshot["fingerprint"],
            "final_fingerprint": after["fingerprint"],
            "forced_after_external_change": contaminated,
        }

    def rollback(self, tx_id: str | None, *, client_id: int, max_steps: int = 128,
                 force: bool = False) -> dict[str, Any]:
        tx = self._require(tx_id, client_id)
        self._assert_safe_or_forced(tx, force=force, action="rollback")
        target = tx.begin_snapshot["fingerprint"]
        current = scene_snapshot(level=DEEP)
        contaminated = tx.external_change_fingerprint is not None

        if current["fingerprint"] == target:
            # A no-op rollback is honest even where undo cannot restore.
            self._finish(tx, ROLLED_BACK, None)
            return {
                "transaction": tx.id,
                "rolled_back": True,
                "state": ROLLED_BACK,
                "undo_steps": 0,
                "fingerprint": target,
                "forced_after_external_change": contaminated,
            }

        undo.assert_functional()
        undo.assert_safe("roll back")
        arrived, steps, current, _sealed = undo.step_back_to(
            target, label=f"RoboVision ROLLBACK {tx.label}", max_steps=max_steps
        )
        if arrived:
            self._finish(tx, ROLLED_BACK, None)
            return {
                "transaction": tx.id,
                "rolled_back": True,
                "state": ROLLED_BACK,
                "undo_steps": steps,
                "fingerprint": target,
                "forced_after_external_change": contaminated,
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
            f"an out-of-band Blender edit occurred during the RoboVision transaction; "
            f"refusing automatic {action}",
            data={
                "transaction": tx.id,
                "begin_fingerprint": tx.begin_snapshot["fingerprint"],
                "external_change_fingerprint": tx.external_change_fingerprint,
                "force_parameter": "Set force=true only if overwriting/including the external edit is intentional.",
            },
        )

    # ------------------------------------------------------------ authority

    def _locate(self, tx_id: str | None) -> Transaction:
        if not tx_id:
            raise HostError("INVALID_PARAMS", "transaction is required")
        self._assert_not_finished(tx_id)
        if self.active is None:
            raise HostError("NO_TRANSACTION", "no transaction is active")
        if tx_id != self.active.id:
            raise HostError("INVALID_PARAMS", "transaction id does not match the open transaction",
                            data={"open": self.active.id})
        return self.active

    def _require(self, tx_id: str | None, client_id: int) -> Transaction:
        """What this client is allowed to do with the transaction it named.

        Ownership and contamination are separate questions, answered in that
        order. Ownership says who is authorised to act; contamination says
        whether acting can still claim what it will affect. An adopted owner can
        be refused for contamination, and an unauthorised client never reaches
        the point where `force` would mean anything.
        """
        tx = self._locate(tx_id)
        if tx.state == ORPHANED:
            raise HostError(
                "TRANSACTION_ORPHANED",
                "that transaction is waiting to be adopted by the client that opened it",
                data={"transaction": tx.id, "reason": tx.reason,
                      "remedy": "transaction.adopt with the recovery token issued at begin"},
                retryable=True,
            )
        if tx.owner_client != client_id:
            raise self._foreign(tx, client_id)
        return tx

    def assert_mutation_allowed(self, client_id: int) -> None:
        """A mutation outside its transaction's owner is refused; reads are not."""
        tx = self.active
        if tx is None:
            return
        if tx.state == ORPHANED:
            raise HostError(
                "TRANSACTION_ORPHANED",
                "the open transaction is waiting to be adopted before it can be continued",
                data={"active": self.state(),
                      "remedy": "transaction.adopt with the recovery token issued at begin"},
                retryable=True,
            )
        if tx.owner_client != client_id:
            raise self._foreign(tx, client_id)

    @staticmethod
    def _foreign(tx: Transaction, client_id: int) -> HostError:
        return HostError(
            "TRANSACTION_FOREIGN",
            "another client holds the open transaction",
            data={"transaction": tx.id, "your_client": client_id},
            retryable=True,
        )

    def _assert_not_finished(self, tx_id: str) -> None:
        record = self.finished.get(tx_id)
        if record is None:
            return
        reason = record.get("reason")
        if reason == "document_changed":
            raise HostError(
                "DOCUMENT_CHANGED",
                "the transaction was abandoned because a different document was loaded",
                data=dict(record),
            )
        code = "TRANSACTION_ABANDONED" if record["state"] == ABANDONED else "TRANSACTION_FINISHED"
        raise HostError(
            code,
            f"that transaction is no longer open: {record['state']}"
            + (f" ({reason})" if reason else ""),
            data=dict(record),
        )
