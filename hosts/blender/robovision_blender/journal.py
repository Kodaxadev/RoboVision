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
- the journal is scoped to one document incarnation and resets with it

Anything that has to be *proved* still compares deep fingerprints.
"""
from __future__ import annotations

from collections import deque
import time
from typing import Any, Iterable

from .registry import HostError

# How many events one epoch retains before the oldest are dropped. A client that
# falls further behind than this is told to resynchronise rather than handed a
# partial history it would mistake for a complete one.
RETAINED_EVENTS = 512

OBJECT_CREATED = "OBJECT_CREATED"
OBJECT_DELETED = "OBJECT_DELETED"
OBJECT_CHANGED = "OBJECT_CHANGED"
TOPOLOGY_CHANGED = "TOPOLOGY_CHANGED"
RESYNC_REQUIRED = "RESYNC_REQUIRED"
DOCUMENT_OPENED = "DOCUMENT_OPENED"

AGENT = "agent"
EDITOR = "editor"
HOST = "host"


class ChangeJournal:
    """Per-document-incarnation history of what changed and who changed it."""

    def __init__(self) -> None:
        self.epoch = 1
        self.certain = True
        self.sequence = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=RETAINED_EVENTS)
        self._uncertain_reason: str | None = None

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

        Both the agent's own mutations and changes noticed at resync go through
        here, so attribution is the only thing that differs between them and the
        two paths cannot drift apart.
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
        """A full read of the scene. Opens a new epoch if certainty was lost."""
        if self.certain:
            return False
        self.epoch += 1
        self.certain = True
        self._uncertain_reason = None
        self.sequence = 0
        self._events.clear()
        return True

    def document_opened(self, *, document_incarnation: str) -> None:
        self.epoch = 1
        self.certain = True
        self._uncertain_reason = None
        self.sequence = 0
        self._events.clear()
        self._append(DOCUMENT_OPENED, revision=0, source=HOST,
                     detail={"document_incarnation": document_incarnation})

    # ---------------------------------------------------------------- reading

    def state(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "certain": self.certain,
            "sequence": self.sequence,
            "uncertain_reason": self._uncertain_reason,
            "retained": len(self._events),
        }

    def changes_since(self, after: int, epoch: int | None = None) -> dict[str, Any]:
        if not isinstance(after, int) or isinstance(after, bool) or after < 0:
            raise HostError("INVALID_PARAMS", "after must be a non-negative integer")

        if epoch is not None:
            if not isinstance(epoch, int) or isinstance(epoch, bool):
                raise HostError("INVALID_PARAMS", "epoch must be an integer")
            if epoch != self.epoch:
                raise HostError(
                    "EPOCH_SUPERSEDED",
                    "the journal has opened a new certainty epoch since that sequence was issued",
                    data={"your_epoch": epoch, "current_epoch": self.epoch},
                    retryable=True,
                )

        oldest = self._events[0]["sequence"] if self._events else self.sequence
        # `after` is exclusive, so asking for everything after the last event a
        # client already has is always answerable, even at the retention edge.
        if after < oldest - 1:
            raise HostError(
                "SEQUENCE_TOO_OLD",
                "the journal no longer retains that far back; take an authoritative snapshot",
                data={"requested_after": after, "oldest_retained": oldest, "epoch": self.epoch},
                retryable=True,
            )

        events = [event for event in self._events if event["sequence"] > after]
        return {
            **self.state(),
            "events": events,
            "has_more": False,
        }
