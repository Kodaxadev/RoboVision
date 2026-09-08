from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .errors import ErrorPayload, RoboVisionError

PROTOCOL_VERSION = "1.0"


@dataclass(slots=True)
class Request:
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    if_revision: int | None = None
    rv: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rv": self.rv, "id": self.id, "method": self.method, "params": self.params}
        if self.if_revision is not None:
            out["if_revision"] = self.if_revision
        return out

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Request":
        if not isinstance(value, dict):
            raise RoboVisionError("INVALID_REQUEST", "request must be an object")
        if value.get("rv") != PROTOCOL_VERSION:
            raise RoboVisionError("PROTOCOL_MISMATCH", f"expected protocol {PROTOCOL_VERSION}")
        method = value.get("method")
        if not isinstance(method, str) or not method:
            raise RoboVisionError("INVALID_REQUEST", "method must be a non-empty string")
        params = value.get("params", {})
        if not isinstance(params, dict):
            raise RoboVisionError("INVALID_REQUEST", "params must be an object")
        rid = value.get("id")
        if not isinstance(rid, str) or not rid:
            raise RoboVisionError("INVALID_REQUEST", "id must be a non-empty string")
        revision = value.get("if_revision")
        if revision is not None and (not isinstance(revision, int) or isinstance(revision, bool) or revision < 0):
            raise RoboVisionError("INVALID_REQUEST", "if_revision must be a non-negative integer")
        return cls(method=method, params=params, id=rid, if_revision=revision, rv=value["rv"])


@dataclass(slots=True)
class Response:
    id: str
    ok: bool
    revision: int
    result: Any = None
    error: ErrorPayload | None = None
    warnings: list[str] = field(default_factory=list)
    timing_ms: float | None = None
    rv: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rv": self.rv, "id": self.id, "ok": self.ok, "revision": self.revision}
        if self.ok:
            out["result"] = self.result
        elif self.error is not None:
            out["error"] = self.error.to_dict()
        if self.warnings:
            out["warnings"] = self.warnings
        if self.timing_ms is not None:
            out["timing_ms"] = round(self.timing_ms, 3)
        return out
