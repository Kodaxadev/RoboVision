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
    # A client-chosen, non-secret handle for the request that opened this
    # transaction. It grants nothing: it exists so that a client whose begin
    # acknowledgement was lost can recognise which transaction its precommitted
    # secret belongs to, without the host id ever becoming client-authoritative.
    handle: str | None = None
    # Which credential is current, counted rather than named. After an adoption
    # whose reply was lost, this is what tells a client whether the rotation
    # happened — an ambiguous acknowledgement becomes a deterministic lookup.
    # Non-secret by construction: it identifies no verifier and authenticates
    # nothing.
    recovery_generation: int = 0
    state: str = ACTIVE
    reason: str | None = None
    external_change_fingerprint: str | None = None


class TransactionManager:
    def __init__(self, on_finished=None) -> None:
        # Called with (transaction id, terminal state) when one ends. The
        # operations recorded inside a transaction keep their tombstones and
        # learn what became of it; discarding those records with the transaction
        # would reopen the hole idempotency exists to close — operation executes,
        # reply is lost, transaction rolls back, client retries, and with the key
        # gone it executes again against a scene where the first one was undone.
        self.on_finished = on_finished
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

    def _finish(self, tx: Transaction, state: str, reason: str | None,
                evidence: dict[str, Any] | None = None) -> None:
        record: dict[str, Any] = {"transaction": tx.id, "state": state,
                                  "world_incarnation": tx.world,
                                  "recovery_handle": tx.handle,
                                  "recovery_generation": tx.recovery_generation}
        if reason is not None:
            record["reason"] = reason
        # Terminal state instead of replaying a stored result is fine, but only
        # if the terminal answer still carries what made it meaningful. A caller
        # whose commit reply was lost needs the proof, not just the word.
        if evidence:
            record.update(evidence)
        self.finished[tx.id] = record
        while len(self.finished) > 32:
            self.finished.popitem(last=False)
        self.active = None
        if self.on_finished is not None:
            self.on_finished(tx.id, state)

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
            "recovery_handle": tx.handle,
            "recovery_generation": tx.recovery_generation,
            "verified_rollback_available": not bpy.app.background,
            **({"reason": tx.reason} if tx.reason else {}),
        }

    # ----------------------------------------------------------------- begin

    def begin(self, label: str, *, world: str, revision: int, owner_client: int,
              before: dict[str, Any] | None = None,
              recovery_verifier: str | None = None,
              recovery_handle: str | None = None) -> dict[str, Any]:
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
        # Precommitted where the client supplied a verifier: it already holds the
        # secret, so a lost reply cannot strand the transaction. Minted only as a
        # fallback, and the response says which happened rather than letting the
        # weaker path look like the safe one.
        secret: str | None = None
        if isinstance(recovery_verifier, str) and recovery_verifier:
            token = RecoveryToken.precommit(recovery_verifier)
        else:
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
            handle=str(recovery_handle) if recovery_handle else None,
        )
        response = {
            **self.state(),
            "begin_fingerprint": before["fingerprint"],
            # Stated up front rather than discovered at rollback time.
            "verified_rollback": not bpy.app.background,
            "recovery_precommitted": token.precommitted,
        }
        if secret is not None:
            # Returned exactly once, and only because the caller did not
            # precommit. If this reply is lost the transaction cannot be
            # reclaimed, which is why precommitting is the documented path.
            response["recovery_token"] = secret
            response["recovery_token_note"] = (
                "Store this. It is the only way to reclaim this transaction after a disconnect, "
                "and the host keeps only a hash of it. Prefer sending recovery_verifier at begin "
                "so a lost reply cannot strand the transaction."
            )
        return response

    def mark_external_change(self, fingerprint: str) -> None:
        tx = self.active
        if tx is None or fingerprint == tx.begin_snapshot["fingerprint"]:
            return
        if tx.external_change_fingerprint is None:
            tx.external_change_fingerprint = fingerprint

    # ----------------------------------------------------------------- adopt

    def adopt(self, tx_id: str | None, offered: str | None, *, client_id: int,
              next_verifier: str | None = None) -> dict[str, Any]:
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
        # Rotation is what stops a leaked secret being a permanent key, and it is
        # also where a lost reply does the most damage: the old secret is dead
        # the moment this returns. A client that supplies the verifier for its
        # next secret already holds the replacement, so losing this reply costs
        # it nothing.
        rotated: str | None = None
        if isinstance(next_verifier, str) and next_verifier:
            tx.recovery = RecoveryToken.precommit(next_verifier)
        else:
            tx.recovery, rotated = RecoveryToken.mint()
        # Counted after the swap, so the number a client reads back is the
        # generation of the credential that is now current.
        tx.recovery_generation += 1
        response = {
            **self.state(),
            "adopted": True,
            "recovery_precommitted": tx.recovery.precommitted,
        }
        if rotated is not None:
            response["recovery_token"] = rotated
            response["recovery_token_note"] = (
                "Rotated by this adoption; the previous token no longer works. Send "
                "next_recovery_verifier with adopt so a lost reply cannot strand the transaction."
            )
        return response

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
        self._finish(tx, COMMITTED, None, {
            "begin_fingerprint": tx.begin_snapshot["fingerprint"],
            "final_fingerprint": after["fingerprint"],
            "forced_after_external_change": contaminated,
        })
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
            self._finish(tx, ROLLED_BACK, None, {
                "restored_fingerprint": target, "undo_steps": 0,
                "forced_after_external_change": contaminated,
            })
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
            self._finish(tx, ROLLED_BACK, None, {
                "restored_fingerprint": target, "undo_steps": steps,
                "forced_after_external_change": contaminated,
            })
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

    def status(self, tx_id: str | None) -> dict[str, Any]:
        """What became of a transaction, without touching it.

        A caller whose commit acknowledgement was lost needs to learn the outcome
        without repeating the operation to discover it. Reading is the whole
        point: this never opens, finishes or mutates anything.
        """
        active = self.state()
        if tx_id is None:
            return {"active": active, "finished": None}
        if active is not None and active["transaction"] == tx_id:
            return {"active": active, "finished": None}
        record = self.finished.get(tx_id)
        if record is None:
            raise HostError(
                "NOT_FOUND",
                "no open or recently finished transaction has that id",
                data={"transaction": tx_id},
            )
        return {"active": None, "finished": dict(record)}

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
