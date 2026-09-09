"""A lost reply must not become a second bevel.

Measured before this existed, on a host with no deduplication at all: a resent
`object.create` produced two objects; a resent `object.delete` reported
`NOT_FOUND` for an operation that had succeeded; a resent absolute transform
reported `noop` and looked protected, which it was not — it was merely an
operation whose second application happened not to move anything. Three
different accidents, none of them a guarantee.

The scope of a logical invocation is `(world, transaction-or-none, key)`. The
world, because an operation was planned against one editing context and a
retry must never be reinterpreted in another. Not the bridge: that can be
rebuilt while the world it was editing is verifiably still open, and the
invocation is the same invocation.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .registry import HostError

RESERVED = "reserved"
TERMINAL = "terminal"
INTERRUPTED = "interrupted"


@dataclass(slots=True)
class Invocation:
    key: str
    recipe: str
    world: str
    transaction: str | None
    state: str
    intent_sequence: int
    response: dict[str, Any] | None = None
    transaction_outcome: str | None = None


@dataclass(slots=True)
class IdempotencyLedger:
    """Which logical invocations this world has seen, and how they ended."""

    records: OrderedDict[str, Invocation] = field(default_factory=OrderedDict)
    retained: int = 512

    # ------------------------------------------------------------- lookup

    def _refuse(self, code: str, message: str, **data: Any) -> HostError:
        return HostError(code, message, data=data)

    def check(self, key: str, recipe: str, attempt: int, world: str) -> dict[str, Any] | None:
        """Decide what a delivery is: first, duplicate, mismatch or unknowable.

        Returns the stored response to replay, or None to execute. Raises when
        the delivery must not execute and has nothing to replay.
        """
        record = self.records.get(key)

        if record is None:
            if attempt > 1:
                # The honest case, and the one the written contract used to get
                # wrong. A forgotten key and a never-seen key are indistinguishable
                # to the host, so a client that says it is retrying must not be
                # answered by executing: the first attempt may have applied.
                raise self._refuse(
                    "INDETERMINATE",
                    "this is a retry of an operation this host has no record of; "
                    "it may already have been applied",
                    idempotency_key=key, attempt=attempt,
                    remedy="Re-observe the scene, then issue a new operation with a new key.",
                )
            return None

        if record.recipe != recipe:
            # Same key, different computation. Executing either one would be a
            # guess about which the client meant.
            raise self._refuse(
                "IDEMPOTENCY_MISMATCH",
                "that idempotency key was used for a different operation",
                idempotency_key=key,
                recorded_recipe=record.recipe,
                requested_recipe=recipe,
            )

        if record.world != world:
            raise self._refuse(
                "STALE_WORLD",
                "that operation was planned against an editing context that is no longer open",
                idempotency_key=key, recorded_world=record.world, current_world=world,
            )

        if record.state == RESERVED:
            raise self._refuse(
                "IN_PROGRESS",
                "that operation is already executing",
                idempotency_key=key,
            )

        if record.state == INTERRUPTED:
            raise self._refuse(
                "INDETERMINATE",
                "that operation was interrupted before its outcome was recorded",
                idempotency_key=key,
                remedy="Re-observe the scene; the operation may or may not have applied.",
            )

        response = dict(record.response or {})
        response["replayed"] = True
        if record.transaction_outcome:
            # A retry after the transaction ended is answered with what happened
            # to it, rather than being executed again as if it were new work.
            response["transaction_outcome"] = record.transaction_outcome
        return response

    # ------------------------------------------------------------ writing

    def reserve(self, key: str, recipe: str, world: str, transaction: str | None,
                intent_sequence: int) -> None:
        self.records[key] = Invocation(
            key=key, recipe=recipe, world=world, transaction=transaction,
            state=RESERVED, intent_sequence=intent_sequence,
        )
        self._trim()

    def complete(self, key: str, response: dict[str, Any]) -> None:
        record = self.records.get(key)
        if record is None:
            return
        record.state = TERMINAL
        record.response = dict(response)

    def release(self, key: str) -> None:
        """An execution that never reached a terminal outcome leaves evidence.

        Not deleted: a reservation that is dropped silently is indistinguishable
        from a key that was never used, which is exactly the confusion this
        module exists to prevent.
        """
        record = self.records.get(key)
        if record is not None and record.state == RESERVED:
            record.state = INTERRUPTED

    def forget(self, key: str) -> None:
        """An operation that provably did not apply leaves no claim behind.

        Only ever called where automatic recovery restored the pre-operation
        fingerprint, which is proof that nothing happened. Anything less certain
        keeps its record and answers INDETERMINATE.
        """
        self.records.pop(key, None)

    def note_transaction_outcome(self, transaction: str, outcome: str) -> None:
        """A transaction ended; its operations keep their tombstones.

        The proposal said transaction-scoped records are discarded with the
        transaction. Taken literally that reopens the hole: operation executes,
        reply is lost, transaction rolls back, client retries — and with the key
        gone it executes again, against a scene where the first one was undone.
        The record stays and reports what became of the transaction instead.
        """
        for record in self.records.values():
            if record.transaction == transaction:
                record.transaction_outcome = outcome

    def adopt(self, resumed: dict[str, dict[str, Any]], world: str) -> None:
        """Rebuild what a previous bridge knew about this world, from the ledger."""
        for key, pair in resumed.items():
            intent = pair.get("intent") or {}
            result = pair.get("result")
            record = Invocation(
                key=key,
                recipe=intent.get("recipe_hash", ""),
                world=intent.get("world", world),
                transaction=intent.get("transaction"),
                state=TERMINAL if result else INTERRUPTED,
                intent_sequence=int(intent.get("sequence", 0)),
                response=(result or {}).get("response"),
            )
            self.records[key] = record
        self._trim()

    def _trim(self) -> None:
        while len(self.records) > self.retained:
            self.records.popitem(last=False)
