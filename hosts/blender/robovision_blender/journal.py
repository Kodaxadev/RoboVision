"""A change journal that never lets a cheap answer masquerade as a proof.

Deep fingerprints are correct and, on a large scene, too expensive to take after
every action. The journal exists so an agent can ask "what moved since I last
looked?" instead of re-reading the whole scene. It is a performance
optimisation, and it is built so that it cannot quietly become something an
agent trusts more than it should:

- every event names stable ids, never names
- events are attributed to the agent's own request or to the editor
- when the host cannot say what changed, it says so and stops claiming certainty
- certainty is an epoch, and losing it is sticky: only an authoritative snapshot
  opens a new epoch, never a later quiet-looking notification
- the journal is scoped to one world incarnation and resets with it
- a client's position is a cursor that names the world it was issued in, the run
  of the journal that issued it and the certainty epoch it belongs to, so a
  position from a world that no longer exists — or from a history that was
  rebuilt inside a world that does — is refused rather than silently
  reinterpreted against the current one
- every cursor refusal carries `current_cursor`, so a client always learns where
  to resume rather than only that it cannot continue

Anything that has to be *proved* still compares deep fingerprints.
"""
from __future__ import annotations

from collections import deque
import time
import uuid
from typing import Any, Iterable

from .registry import HostError

# How many events one epoch retains before the oldest are dropped. A client that
# falls further behind than this is told to resynchronise rather than handed a
# partial history it would mistake for a complete one.
RETAINED_EVENTS = 512

CURSOR_PREFIX = "rvcursor"

OBJECT_CREATED = "OBJECT_CREATED"
OBJECT_DELETED = "OBJECT_DELETED"
OBJECT_CHANGED = "OBJECT_CHANGED"
TOPOLOGY_CHANGED = "TOPOLOGY_CHANGED"
IDENTITY_REPAIRED = "IDENTITY_REPAIRED"
RESYNC_REQUIRED = "RESYNC_REQUIRED"
WORLD_OPENED = "WORLD_OPENED"
BRIDGE_ATTACHED = "BRIDGE_ATTACHED"

AGENT = "agent"
EDITOR = "editor"
HOST = "host"


class ChangeJournal:
    """Per-world-incarnation history of what changed and who changed it."""

    def __init__(self, world_incarnation: str) -> None:
        self.world_incarnation = world_incarnation
        # Which run of the journal this is inside that world. A world can
        # outlive the history kept about it — a bridge can be rebuilt while the
        # file stays open — and every counter in a cursor restarts at 1 when
        # that happens, so only a separate identity keeps an old position from
        # resolving against a history that has never seen it.
        self.journal_incarnation = "rvjournal:" + str(uuid.uuid4())
        self.epoch = 1
        self.certain = True
        self.sequence = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=RETAINED_EVENTS)
        self._uncertain_reason: str | None = None

    # ---------------------------------------------------------------- cursors

    @staticmethod
    def _tail(incarnation: str) -> str:
        # Incarnations are `<kind>:<uuid>`; the kind is dropped so a cursor stays
        # five colon-separated fields and parses unambiguously.
        _, _, tail = incarnation.partition(":")
        return tail or incarnation

    def cursor(self) -> str:
        """The client's position, bound to the history that issued it.

        A bare sequence number is not a position. Sequence restarts at a new
        certainty epoch, at a new journal and at a new world, so the same
        integer names different moments in different histories — and every one
        of those counters starts again from the same number, so no counter
        separates them either. Only the two incarnations do.
        """
        return (
            f"{CURSOR_PREFIX}:{self._tail(self.journal_incarnation)}"
            f":{self._tail(self.world_incarnation)}:{self.epoch}:{self.sequence}"
        )

    def _refuse(self, code: str, message: str, *, cursor: Any, retryable: bool = False,
                **extra: Any) -> HostError:
        """Every cursor refusal says where to resume from.

        A client that cannot continue needs one thing to recover, and it is the
        same thing in all six cases, so it is attached in one place rather than
        remembered at each raise.
        """
        return HostError(
            code,
            message,
            data={"cursor": cursor, "current_cursor": self.cursor(), **extra},
            retryable=retryable,
        )

    def _parse_cursor(self, cursor: Any) -> int:
        """Check a cursor against this journal.

        What this establishes: the cursor is well formed, names the world that
        is open now and the journal running in it, names the current certainty
        epoch, and falls inside the retained range. What it does not establish
        is issuance.
        Nothing here is signed, so a client that constructs a syntactically
        valid cursor for the current world is indistinguishable from one handed
        the same string. That is deliberate for a read-only journal: a forged
        cursor reads events the caller could already read, while the checks that
        matter are about staleness, which a forger has no reason to fake.
        """
        if not isinstance(cursor, str) or not cursor:
            raise self._refuse("INVALID_PARAMS", "cursor must be a string", cursor=cursor)
        parts = cursor.split(":")
        if len(parts) != 5 or parts[0] != CURSOR_PREFIX:
            raise self._refuse(
                "INVALID_PARAMS",
                f"cursor must look like {CURSOR_PREFIX}:<journal>:<world>:<epoch>:<sequence>",
                cursor=cursor,
            )
        _, journal, world, epoch_text, sequence_text = parts
        try:
            epoch = int(epoch_text)
            sequence = int(sequence_text)
        except ValueError as exc:
            raise self._refuse("INVALID_PARAMS", "cursor epoch and sequence must be integers",
                               cursor=cursor) from exc
        if epoch < 1 or sequence < 0:
            raise self._refuse("INVALID_PARAMS", "cursor epoch and sequence are out of range",
                               cursor=cursor)

        # Order matters, outermost scope first. Every counter in a cursor
        # restarts at 1 in a new world and in a new journal, so a matching epoch
        # proves nothing until both scopes are settled — and the two fail for
        # reasons a client has to act on differently, so they are separate codes
        # rather than one.
        if world != self._tail(self.world_incarnation):
            raise self._refuse(
                "STALE_WORLD",
                "that cursor names an editing context that is no longer open",
                cursor=cursor,
                retryable=True,
                current_world_incarnation=self.world_incarnation,
            )
        if journal != self._tail(self.journal_incarnation):
            raise self._refuse(
                "JOURNAL_REPLACED",
                "the world is still open but this journal did not write that cursor; "
                "its history was rebuilt and is gone",
                cursor=cursor,
                retryable=True,
                current_journal_incarnation=self.journal_incarnation,
            )
        if epoch != self.epoch:
            raise self._refuse(
                "EPOCH_SUPERSEDED",
                "the journal has opened a new certainty epoch since that cursor was current",
                cursor=cursor,
                retryable=True,
                your_epoch=epoch,
                current_epoch=self.epoch,
            )
        if sequence > self.sequence:
            # Same world, same journal, same epoch, ahead of everything that
            # has happened.
            # No position in this journal carries that number yet.
            raise self._refuse(
                "INVALID_PARAMS",
                "that cursor is ahead of the journal; no such position exists yet",
                cursor=cursor,
            )
        return sequence

    # ---------------------------------------------------------------- writing

    def _append(self, event_type: str, *, revision: int, source: str,
                ids: Iterable[str] = (), path: str | None = None,
                request: str | None = None, detail: Any = None) -> dict[str, Any]:
        self.sequence += 1
        event = {
            "sequence": self.sequence,
            "epoch": self.epoch,
            "revision": revision,
            "type": event_type,
            "ids": sorted(ids),
            "source": source,
            "time": round(time.time(), 3),
        }
        if path is not None:
            event["path"] = path
        if request is not None:
            event["request"] = request
        if detail is not None:
            event["detail"] = detail
        self._events.append(event)
        return event

    def record_diff(self, diff: dict[str, Any], *, revision: int, source: str,
                    request: str | None = None) -> int:
        """Turn a snapshot diff into attributed events.

        Both the agent's own mutations and changes discovered at reconciliation
        go through here, so attribution is the only thing that differs between
        them and the two paths cannot drift apart.
        """
        written = 0
        for entry in diff.get("created", []):
            self._append(OBJECT_CREATED, revision=revision, source=source,
                         ids=[entry["id"]], request=request)
            written += 1
        for entry in diff.get("deleted", []):
            self._append(OBJECT_DELETED, revision=revision, source=source,
                         ids=[entry["id"]], request=request)
            written += 1
        for entry in diff.get("changed", []):
            before = entry.get("before") or {}
            after = entry.get("after") or {}
            before_mesh = (before.get("mesh") or {})
            after_mesh = (after.get("mesh") or {})
            topology_moved = (
                before_mesh.get("topology") != after_mesh.get("topology")
                or before_mesh.get("revision") != after_mesh.get("revision")
            )
            self._append(
                TOPOLOGY_CHANGED if topology_moved else OBJECT_CHANGED,
                revision=revision,
                source=source,
                ids=[entry["id"]],
                request=request,
            )
            written += 1
        return written

    def record_identity_repair(self, repair: dict[str, str], *, revision: int) -> None:
        """The host put its own bookkeeping back; nothing an author wrote moved.

        `_robovision_id` is a custom property and anyone can delete or rewrite
        it. Before this, doing so minted a replacement id, and the change
        reached the agent as a deletion and a creation — the scene claiming an
        object had been destroyed when only the host's record of its name had
        been damaged. The event carries the basis for the claim, because a
        restored identity is only worth as much as the evidence behind it.
        """
        self._append(
            IDENTITY_REPAIRED,
            revision=revision,
            source=HOST,
            ids=[repair["id"]],
            detail=dict(repair),
        )

    def lose_certainty(self, reason: str, *, revision: int) -> None:
        """Record that the host cannot account for a change.

        Sticky on purpose. A later notification that happens to look clean says
        nothing about the change that was missed, so only an authoritative
        snapshot may restore trust.
        """
        if not self.certain:
            return
        self.certain = False
        self._uncertain_reason = reason
        self._append(RESYNC_REQUIRED, revision=revision, source=HOST, detail={"reason": reason})

    def authoritative_snapshot(self, *, revision: int) -> bool:
        """A full read of the scene. Opens a new epoch if certainty was lost.

        Reconciliation runs before this, so a change the host had missed is
        attributed and journalled first. Certainty is only still lost here when
        that attribution was genuinely impossible, and then the history is not
        salvageable: the sequence restarts rather than being continued across a
        gap the client would have no way to see.
        """
        if self.certain:
            return False
        self.epoch += 1
        self.certain = True
        self._uncertain_reason = None
        self.sequence = 0
        self._events.clear()
        return True

    def rebind(self, world_incarnation: str, *, reason: str) -> None:
        """Start again: a different world, or the same world with no history.

        Both a file load and a bridge reattach reach here. The journal
        incarnation rotates in both cases because the history is gone in both
        cases, which is what a client's cursor depends on; the world incarnation
        is passed in because only the runtime can say whether the editing
        context itself survived.
        """
        self.world_incarnation = world_incarnation
        self.journal_incarnation = "rvjournal:" + str(uuid.uuid4())
        self.epoch = 1
        self.certain = True
        self._uncertain_reason = None
        self.sequence = 0
        self._events.clear()
        self._append(
            WORLD_OPENED if reason == "load" else BRIDGE_ATTACHED,
            revision=0,
            source=HOST,
            detail={
                "world_incarnation": world_incarnation,
                "journal_incarnation": self.journal_incarnation,
                "reason": reason,
            },
        )

    # ---------------------------------------------------------------- reading

    def state(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "certain": self.certain,
            "sequence": self.sequence,
            "cursor": self.cursor(),
            "world_incarnation": self.world_incarnation,
            "journal_incarnation": self.journal_incarnation,
            "uncertain_reason": self._uncertain_reason,
            "retained": len(self._events),
        }

    def bootstrap(self) -> dict[str, Any]:
        """Where the journal is now, for a client that has no position yet.

        Deliberately returns no events. The host does not hand back whatever
        history it happens to still hold, because a client that never had a
        position cannot tell a complete history from a truncated one, and an
        incomplete list read as complete is worse than no list.

        Separate from `changes_since` because "I have no cursor" and "here is my
        cursor, which happens to be null" are different claims, and answering the
        second as if it were the first would report "nothing changed" to a client
        whose position was lost.
        """
        return {
            **self.state(),
            "bootstrap": True,
            "events": [],
            "has_more": False,
        }

    def changes_since(self, cursor: Any) -> dict[str, Any]:
        """Events after `cursor`. A cursor is required; see `bootstrap`."""
        after = self._parse_cursor(cursor)
        oldest = self._events[0]["sequence"] if self._events else self.sequence
        # `after` is exclusive, so asking for everything after the last event a
        # client already has is always answerable, even at the retention edge.
        if after < oldest - 1:
            raise self._refuse(
                "SEQUENCE_TOO_OLD",
                "the journal no longer retains that far back; take an authoritative snapshot",
                cursor=cursor,
                retryable=True,
                oldest_retained=oldest,
                epoch=self.epoch,
            )

        events = [event for event in self._events if event["sequence"] > after]
        return {
            **self.state(),
            "bootstrap": False,
            "events": events,
            "has_more": False,
        }
