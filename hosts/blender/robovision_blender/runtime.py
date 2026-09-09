from __future__ import annotations

from collections import deque
import traceback
import uuid
from typing import Any

import bpy

from . import handlers
from .dispatch import dispatch_request, validate_request
from .identity import forget_identity_owners
from .journal import AGENT, EDITOR, ChangeJournal
from .protocol import HOST_VERSION, PROTOCOL_VERSION
from .registry import HostError, ToolRegistry
from .snapshots import DEEP, diff_snapshots, scene_snapshot
from .transactions import TransactionManager
from .transport import NonBlockingJsonServer

__all__ = ["HOST_VERSION", "PROTOCOL_VERSION", "RUNTIME", "RoboVisionRuntime"]


class RoboVisionRuntime:
    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.transactions = TransactionManager()
        self.transport: NonBlockingJsonServer | None = None
        self.revision = 0
        self._dirty = True
        self._last_fingerprint: str | None = None
        self._last_snapshot: dict[str, Any] | None = None
        self._timer_fn = self._tick
        self._registered_tools = False
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._snapshot_order: deque[str] = deque()
        self._invalidated_snapshots: dict[str, Any] = {}
        # Two identities, deliberately separate. The bridge is this loaded
        # RoboVision runtime: it rotates when the add-on is reloaded or the
        # bridge restarts, and it does not care which file is open. The document
        # incarnation is one loaded state universe: it rotates when a document is
        # opened or replaced, while the bridge keeps running with its sockets
        # still bound. A handle stale for one reason is not stale for the other,
        # and one id could not report both.
        self.bridge = "rvbridge:" + str(uuid.uuid4())
        self.document_incarnation = "rvdoc:" + str(uuid.uuid4())
        self.document: str | None = None
        self.journal = ChangeJournal(self.document_incarnation)

    @property
    def running(self) -> bool:
        return self.transport is not None and self.transport.running

    def start(self, port: int = 9877) -> None:
        if self.running:
            return
        self.registry_ready()
        self.transport = NonBlockingJsonServer(port=port)
        self.transport.start()
        self.install_handlers()
        if not bpy.app.timers.is_registered(self._timer_fn):
            bpy.app.timers.register(self._timer_fn, first_interval=0.02, persistent=True)

    def install_handlers(self) -> None:
        """Subscribe to editor change notifications and take a baseline.

        Exposed separately from `start()` so an integration harness can exercise
        the same out-of-band change detection the add-on uses without opening a
        socket. A test that skipped this would be checking different code from
        the one shipped.

        Only a genuine (re)attach is a new bridge incarnation. Add-on
        disable/enable detaches and attaches again, and a client must be able to
        tell that the code it was talking to has been replaced. Opening a socket
        on an already attached runtime is not that, so attaching twice is
        idempotent. The document incarnation rotates with a real reattach
        because it lives in this bridge's memory, and a reattached bridge cannot
        know what the previous one minted.
        """
        self.registry_ready()
        if handlers.attach(self):
            self.bridge = "rvbridge:" + str(uuid.uuid4())
            self.document_incarnation = "rvdoc:" + str(uuid.uuid4())
            self.journal.rebind(self.document_incarnation, reason="bridge_attached")
            # Nothing the previous attachment remembered belongs to this
            # identity, so the baseline is established fresh rather than diffed
            # against a world that goes by a different name now.
            self._last_fingerprint = None
            self._last_snapshot = None
        self.document = bpy.data.filepath or None
        self.reconcile(source=EDITOR)

    def remove_handlers(self) -> None:
        handlers.detach(self)

    def document_closing(self) -> None:
        """A different document is about to replace this one.

        Everything scoped to the open document dies here rather than surviving
        into a file it does not describe. A transaction is the dangerous case:
        its begin fingerprint describes a document that will not exist, so
        leaving it active made rollback report TRANSACTION_CONTAMINATED, which
        blames a human edit for a whole-document change.
        """
        self.transactions.abandon(reason="document_changed")

    def document_saved(self) -> None:
        """A save can change which file this document is, without loading one.

        Save As writes to a new path and the in-memory document becomes that
        file. Nothing is loaded, so the document incarnation is untouched, but a
        host that only learned its path at load time would keep reporting the
        previous file — or, before the first save, no file at all.
        """
        self.document = bpy.data.filepath or None

    def document_opened(self) -> None:
        forget_identity_owners()
        # Remember what was dropped so a client holding one of these handles is
        # told the world changed, rather than that its snapshot never existed.
        self._invalidated_snapshots.update(self._snapshots)
        while len(self._invalidated_snapshots) > 256:
            self._invalidated_snapshots.pop(next(iter(self._invalidated_snapshots)))
        self._snapshots.clear()
        self._snapshot_order.clear()
        # The bridge is untouched by a file load: same code, same sockets.
        self.document_incarnation = "rvdoc:" + str(uuid.uuid4())
        self.document = bpy.data.filepath or None
        self.revision = 0
        self._last_fingerprint = None
        self._last_snapshot = None
        self.journal.rebind(self.document_incarnation, reason="load")
        self.reconcile(source=EDITOR)

    def registry_ready(self) -> None:
        if self._registered_tools:
            return
        from .ops import register_all

        register_all(self.registry)
        self._registered_tools = True

    def stop(self) -> None:
        if self.transport is not None:
            self.transport.stop()
            self.transport = None
        if bpy.app.timers.is_registered(self._timer_fn):
            bpy.app.timers.unregister(self._timer_fn)
        self.remove_handlers()

    def mark_dirty(self) -> None:
        # Handler callback only marks dirty; it deliberately does not inspect or mutate Blender data.
        self._dirty = True

    def _tick(self) -> float | None:
        if not self.running:
            return None
        try:
            assert self.transport is not None
            self.transport.poll(self.dispatch, command_budget=8)
        except Exception:
            traceback.print_exc()
        return 0.02

    # ------------------------------------------------------------ reconciling

    def reconcile(self, *, source: str = EDITOR, request: str | None = None,
                  baseline: dict[str, Any] | None = None,
                  current: dict[str, Any] | None = None) -> dict[str, Any]:
        """The one place that answers "what is the scene now?".

        Dirty refresh, pre-mutation resync, post-mutation acceptance, an
        authoritative snapshot and failed-mutation recovery all land here. They
        differ only in what they compare against and who the result is
        attributed to.

        They used to be three near-identical bodies, and the differences between
        them were where a missed notification hid: `resync` advanced the scene
        revision on a fingerprint change but journalled nothing, so an editor
        edit the host never heard about was absorbed into the next baseline
        while the journal went on claiming certainty. Everything that moves the
        baseline goes through here so that cannot happen again.
        """
        if current is None:
            current = scene_snapshot(level=DEEP)
        fingerprint = current["fingerprint"]
        if baseline is None:
            baseline = self._last_snapshot
        known = baseline["fingerprint"] if baseline is not None else self._last_fingerprint

        moved = known is not None and fingerprint != known
        if moved:
            if source == EDITOR and self.transactions.active is not None:
                self.transactions.mark_external_change(fingerprint)
            # The scene revision names authoritative scene state, not commands
            # run, so it only advances when that state actually moved.
            self.revision += 1

        self._last_fingerprint = fingerprint
        self._last_snapshot = current
        self._dirty = False

        if moved:
            self._journal_change(baseline, current, source=source, request=request)
        return {"snapshot": current, "moved": moved, "revision": self.revision}

    def _journal_change(self, baseline: dict[str, Any] | None, current: dict[str, Any],
                        *, source: str, request: str | None) -> None:
        """Attribute a change, or admit that it cannot be attributed.

        With the previous snapshot in hand the diff says exactly which objects
        moved and the journal stays certain. Without it the host genuinely
        cannot say what changed, and saying so is the only honest option —
        guessing would make the journal worth less than no journal.
        """
        if baseline is None:
            self.journal.lose_certainty(
                "the scene changed with no prior snapshot to attribute it against",
                revision=self.revision,
            )
            return
        try:
            diff = diff_snapshots(baseline, current)
        except (KeyError, TypeError):
            self.journal.lose_certainty("the change could not be diffed", revision=self.revision)
            return
        if not self.journal.record_diff(diff, revision=self.revision, source=source, request=request):
            self.journal.lose_certainty(
                "the fingerprint moved with no attributable object change",
                revision=self.revision,
            )

    def current_snapshot(self) -> dict[str, Any]:
        """The deep read the last reconciliation established.

        A handler that needs the scene as of this call reads it from here rather
        than taking its own, so two parts of one response cannot end up
        describing two different moments. The dispatcher arranges the
        reconciliation for every tool declared authoritative; the fallback is
        for a caller that has not.
        """
        if self._last_snapshot is None:
            return self.reconcile(source=EDITOR)["snapshot"]
        return self._last_snapshot

    def _refresh_dirty_state(self) -> None:
        if not self._dirty:
            return
        self.reconcile(source=EDITOR)

    def resync(self) -> dict[str, Any]:
        """Re-read the scene authoritatively before a mutation is allowed to run.

        `depsgraph_update_post` is a notification, not a guarantee: a script or
        another add-on can change datablocks and the handler may not have run by
        the time the next request is dispatched. Trusting the dirty flag here
        would let a mutation checkpoint against a scene that no longer exists and
        would let a stale `if_revision` pass the concurrency check. Mutations
        therefore pay for one honest deep read, which also serves as their
        recovery checkpoint.
        """
        return self.reconcile(source=EDITOR)["snapshot"]

    def _accept_own_mutation(self, before: dict[str, Any] | None = None,
                             request_id: str | None = None) -> bool:
        """Take the scene as the agent left it. True if the state actually moved."""
        return self.reconcile(source=AGENT, request=request_id, baseline=before)["moved"]

    def _recover_failed_operation(
        self,
        before: dict[str, Any] | None,
        original: Exception,
        request_id: str | None = None,
    ) -> tuple[HostError | None, dict[str, Any] | None]:
        if before is None:
            return None, None
        try:
            recovery = self.transactions.recover_failed_mutation(before)
            self._last_fingerprint = before["fingerprint"]
            self._last_snapshot = before
            self._dirty = False
            return None, recovery
        except HostError as recovery_error:
            # The mutation left something behind that could not be undone. That
            # residue is ours, so it is reconciled and attributed to the request
            # that caused it, rather than left for the next dirty refresh to
            # blame on a human edit.
            try:
                self.reconcile(source=AGENT, request=request_id, baseline=before)
            except Exception:
                self._dirty = True
                self.journal.lose_certainty(
                    "a failed mutation could not be reconciled", revision=self.revision
                )
            original_payload: dict[str, Any] = {
                "type": type(original).__name__,
                "message": str(original),
            }
            if isinstance(original, HostError):
                original_payload["code"] = original.code
                if original.data is not None:
                    original_payload["data"] = original.data
            recovery_error.data = {
                **(recovery_error.data or {}),
                "original_error": original_payload,
            }
            return recovery_error, None

    # ------------------------------------------------------------- snapshots

    def store_snapshot(self, snapshot: dict[str, Any]) -> str:
        snapshot_id = "snap:" + str(uuid.uuid4())
        self._snapshots[snapshot_id] = snapshot
        self._snapshot_order.append(snapshot_id)
        while len(self._snapshot_order) > 32:
            expired = self._snapshot_order.popleft()
            self._snapshots.pop(expired, None)
        return snapshot_id

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        try:
            return self._snapshots[snapshot_id]
        except KeyError as exc:
            if snapshot_id in self._invalidated_snapshots:
                raise HostError(
                    "STALE_DOCUMENT",
                    "the snapshot was taken in a document incarnation that is no longer loaded",
                    data={"snapshot": snapshot_id, "document_incarnation": self.document_incarnation},
                    retryable=True,
                ) from exc
            raise HostError("NOT_FOUND", f"snapshot not found: {snapshot_id}") from exc

    # ---------------------------------------------------------------- protocol

    def dispatch(self, raw: dict[str, Any]) -> dict[str, Any]:
        return dispatch_request(self, raw)

    def _validate_request(self, raw: dict[str, Any]) -> tuple[str, dict[str, Any], int | None]:
        return validate_request(raw)


RUNTIME = RoboVisionRuntime()
