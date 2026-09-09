from __future__ import annotations

from collections import deque
import time
import traceback
import uuid
from typing import Any

import bpy
from bpy.app.handlers import persistent

from .identity import forget_identity_owners
from .registry import HostError, ToolRegistry
from .snapshots import DEEP, scene_snapshot
from .transactions import TransactionManager
from .transport import NonBlockingJsonServer

PROTOCOL_VERSION = "1.0"
HOST_VERSION = "0.1.0"

# Blender handlers are module-level functions, so the notification fans out to
# every runtime that asked for it. The add-on uses one; an integration harness
# may build its own instance and must see the same change notifications.
_ACTIVE_RUNTIMES: "set[RoboVisionRuntime]" = set()


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
        """
        self.registry_ready()
        # Only a genuine (re)attach is a new bridge incarnation. Add-on
        # disable/enable detaches and attaches again, and a client must be able
        # to tell that the code it was talking to has been replaced. Opening a
        # socket on an already attached runtime is not that, so attaching twice
        # must be idempotent. The document incarnation rotates with a real
        # reattach because it lives in this bridge's memory, and a reattached
        # bridge cannot know what the previous one minted.
        if self not in _ACTIVE_RUNTIMES:
            self.bridge = "rvbridge:" + str(uuid.uuid4())
            self.document_incarnation = "rvdoc:" + str(uuid.uuid4())
        _ACTIVE_RUNTIMES.add(self)
        self.document = bpy.data.filepath or None
        self._dirty = True
        self._refresh_dirty_state()
        if _depsgraph_dirty not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(_depsgraph_dirty)
        if _document_closing not in bpy.app.handlers.load_pre:
            bpy.app.handlers.load_pre.append(_document_closing)
        if _document_opened not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(_document_opened)
        if _document_saved not in bpy.app.handlers.save_post:
            bpy.app.handlers.save_post.append(_document_saved)

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
        self._dirty = True
        self._refresh_dirty_state()

    def remove_handlers(self) -> None:
        _ACTIVE_RUNTIMES.discard(self)
        if not _ACTIVE_RUNTIMES and _depsgraph_dirty in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_dirty)

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

    def _refresh_dirty_state(self) -> None:
        if not self._dirty:
            return
        current = scene_snapshot(level=DEEP)
        fingerprint = current["fingerprint"]
        if self._last_fingerprint is not None and fingerprint != self._last_fingerprint:
            if self.transactions.active is not None:
                self.transactions.mark_external_change(fingerprint)
            self.revision += 1
        self._last_fingerprint = fingerprint
        self._last_snapshot = current
        self._dirty = False

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
        current = scene_snapshot(level=DEEP)
        fingerprint = current["fingerprint"]
        if self._last_fingerprint is not None and fingerprint != self._last_fingerprint:
            if self.transactions.active is not None:
                self.transactions.mark_external_change(fingerprint)
            self.revision += 1
        self._last_fingerprint = fingerprint
        self._last_snapshot = current
        self._dirty = False
        return current

    def _accept_own_mutation(self) -> None:
        current = scene_snapshot(level=DEEP)
        self._last_fingerprint = current["fingerprint"]
        self._last_snapshot = current
        self._dirty = False
        self.revision += 1

    def _recover_failed_operation(self, before: dict[str, Any] | None, original: Exception) -> tuple[HostError | None, dict[str, Any] | None]:
        if before is None:
            return None, None
        try:
            recovery = self.transactions.recover_failed_mutation(before)
            self._last_fingerprint = before["fingerprint"]
            self._last_snapshot = before
            self._dirty = False
            return None, recovery
        except HostError as recovery_error:
            self._dirty = True
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

    def dispatch(self, raw: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        request_id = raw.get("id") if isinstance(raw.get("id"), str) else "invalid"
        mutation_before: dict[str, Any] | None = None
        try:
            self._refresh_dirty_state()
            method, params, if_revision = self._validate_request(raw)
            spec = self.registry.get(method)
            if spec.requires_ui and bpy.app.background:
                raise HostError("INVALID_CONTEXT", f"{method} requires an interactive Blender UI")

            is_transaction_control = method.startswith("transaction.")
            checkpoint: dict[str, Any] | None = None
            if spec.mutating:
                # Establish the truth first: the concurrency check below is only
                # meaningful against a revision that reflects the scene as it is
                # right now, not as the last notification left it.
                checkpoint = self.resync()
            if spec.mutating and if_revision is not None and if_revision != self.revision:
                raise HostError(
                    "STALE_REVISION",
                    "scene revision changed",
                    data={"expected": if_revision, "actual": self.revision},
                    retryable=True,
                )

            if checkpoint is not None and not is_transaction_control:
                mutation_before = self.transactions.prepare_mutation(method, checkpoint)

            result = spec.handler(params, self)

            if spec.mutating and method not in {"transaction.begin", "transaction.commit"}:
                self._accept_own_mutation()
            elapsed = (time.perf_counter() - started) * 1000.0
            return {
                "rv": PROTOCOL_VERSION,
                "id": request_id,
                "ok": True,
                "revision": self.revision,
                "result": result,
                "timing_ms": round(elapsed, 3),
            }
        except HostError as exc:
            recovery_error, recovery = self._recover_failed_operation(mutation_before, exc)
            if recovery_error is not None:
                exc = recovery_error
            elapsed = (time.perf_counter() - started) * 1000.0
            data = exc.data
            if recovery is not None:
                data = {"operation_error_data": exc.data, "automatic_recovery": recovery}
            error: dict[str, Any] = {"code": exc.code, "message": str(exc), "retryable": exc.retryable}
            if data is not None:
                error["data"] = data
            return {
                "rv": PROTOCOL_VERSION,
                "id": request_id,
                "ok": False,
                "revision": self.revision,
                "error": error,
                "timing_ms": round(elapsed, 3),
            }
        except Exception as exc:
            recovery_error, recovery = self._recover_failed_operation(mutation_before, exc)
            if recovery_error is not None:
                elapsed = (time.perf_counter() - started) * 1000.0
                error: dict[str, Any] = {
                    "code": recovery_error.code,
                    "message": str(recovery_error),
                    "retryable": recovery_error.retryable,
                }
                if recovery_error.data is not None:
                    error["data"] = recovery_error.data
                return {
                    "rv": PROTOCOL_VERSION,
                    "id": request_id,
                    "ok": False,
                    "revision": self.revision,
                    "error": error,
                    "timing_ms": round(elapsed, 3),
                }
            traceback.print_exc()
            elapsed = (time.perf_counter() - started) * 1000.0
            error_data = {"automatic_recovery": recovery} if recovery is not None else None
            response: dict[str, Any] = {
                "rv": PROTOCOL_VERSION,
                "id": request_id,
                "ok": False,
                "revision": self.revision,
                "error": {
                    "code": "HOST_EXCEPTION",
                    "message": f"{type(exc).__name__}: host operation failed; see Blender console for traceback",
                    "retryable": False,
                },
                "timing_ms": round(elapsed, 3),
            }
            if error_data is not None:
                response["error"]["data"] = error_data
            return response

    def _validate_request(self, raw: dict[str, Any]) -> tuple[str, dict[str, Any], int | None]:
        if raw.get("rv") != PROTOCOL_VERSION:
            raise HostError("PROTOCOL_MISMATCH", f"expected protocol {PROTOCOL_VERSION}")
        if not isinstance(raw.get("id"), str) or not raw["id"]:
            raise HostError("INVALID_REQUEST", "id must be a non-empty string")
        method = raw.get("method")
        if not isinstance(method, str) or not method:
            raise HostError("INVALID_REQUEST", "method must be a non-empty string")
        params = raw.get("params", {})
        if not isinstance(params, dict):
            raise HostError("INVALID_REQUEST", "params must be an object")
        if_revision = raw.get("if_revision")
        if if_revision is not None and (not isinstance(if_revision, int) or isinstance(if_revision, bool) or if_revision < 0):
            raise HostError("INVALID_REQUEST", "if_revision must be a non-negative integer")
        return method, params, if_revision


RUNTIME = RoboVisionRuntime()


@persistent
def _depsgraph_dirty(_scene=None, _depsgraph=None) -> None:
    for runtime in tuple(_ACTIVE_RUNTIMES):
        runtime.mark_dirty()


@persistent
def _document_closing(_file=None, _other=None) -> None:
    for runtime in tuple(_ACTIVE_RUNTIMES):
        runtime.document_closing()


@persistent
def _document_opened(_file=None, _other=None) -> None:
    for runtime in tuple(_ACTIVE_RUNTIMES):
        runtime.document_opened()


@persistent
def _document_saved(_file=None, _other=None) -> None:
    for runtime in tuple(_ACTIVE_RUNTIMES):
        runtime.document_saved()
