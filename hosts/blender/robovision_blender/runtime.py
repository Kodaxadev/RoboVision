from __future__ import annotations

from collections import deque
import time
import traceback
import uuid
from typing import Any

import bpy
from bpy.app.handlers import persistent

from .registry import HostError, ToolRegistry
from .snapshots import scene_snapshot
from .transactions import TransactionManager
from .transport import NonBlockingJsonServer

PROTOCOL_VERSION = "1.0"
HOST_VERSION = "0.1.0"


class RoboVisionRuntime:
    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.transactions = TransactionManager()
        self.transport: NonBlockingJsonServer | None = None
        self.revision = 0
        self._dirty = True
        self._last_fingerprint: str | None = None
        self._timer_fn = self._tick
        self._registered_tools = False
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._snapshot_order: deque[str] = deque()

    @property
    def running(self) -> bool:
        return self.transport is not None and self.transport.running

    def start(self, port: int = 9877) -> None:
        if self.running:
            return
        if not self._registered_tools:
            from .ops import register_all

            register_all(self.registry)
            self._registered_tools = True
        self.transport = NonBlockingJsonServer(port=port)
        self.transport.start()
        self._dirty = True
        self._refresh_dirty_state()
        if _depsgraph_dirty not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(_depsgraph_dirty)
        if not bpy.app.timers.is_registered(self._timer_fn):
            bpy.app.timers.register(self._timer_fn, first_interval=0.02, persistent=True)

    def stop(self) -> None:
        if self.transport is not None:
            self.transport.stop()
            self.transport = None
        if bpy.app.timers.is_registered(self._timer_fn):
            bpy.app.timers.unregister(self._timer_fn)
        if _depsgraph_dirty in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_dirty)

    def mark_dirty(self) -> None:
        # Called from a depsgraph handler; deliberately does not inspect/mutate Blender data.
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
        current = scene_snapshot(deep=True)
        fingerprint = current["fingerprint"]
        if self._last_fingerprint is not None and fingerprint != self._last_fingerprint:
            self.revision += 1
        self._last_fingerprint = fingerprint
        self._dirty = False

    def _accept_own_mutation(self) -> None:
        current = scene_snapshot(deep=True)
        self._last_fingerprint = current["fingerprint"]
        self._dirty = False
        self.revision += 1

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
            raise HostError("NOT_FOUND", f"snapshot not found: {snapshot_id}") from exc

    def dispatch(self, raw: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        request_id = raw.get("id") if isinstance(raw.get("id"), str) else "invalid"
        try:
            self._refresh_dirty_state()
            method, params, if_revision = self._validate_request(raw)
            spec = self.registry.get(method)
            if spec.requires_ui and bpy.app.background:
                raise HostError("INVALID_CONTEXT", f"{method} requires an interactive Blender UI")
            if spec.mutating and if_revision is not None and if_revision != self.revision:
                raise HostError(
                    "STALE_REVISION",
                    "scene revision changed",
                    data={"expected": if_revision, "actual": self.revision},
                    retryable=True,
                )

            is_transaction_control = method.startswith("transaction.")
            if spec.mutating and not is_transaction_control:
                self.transactions.prepare_standalone_mutation(method)

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
            elapsed = (time.perf_counter() - started) * 1000.0
            error: dict[str, Any] = {"code": exc.code, "message": str(exc), "retryable": exc.retryable}
            if exc.data is not None:
                error["data"] = exc.data
            return {
                "rv": PROTOCOL_VERSION,
                "id": request_id,
                "ok": False,
                "revision": self.revision,
                "error": error,
                "timing_ms": round(elapsed, 3),
            }
        except Exception as exc:
            traceback.print_exc()
            elapsed = (time.perf_counter() - started) * 1000.0
            return {
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
    RUNTIME.mark_dirty()
