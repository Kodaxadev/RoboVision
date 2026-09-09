from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .errors import ErrorPayload, RoboVisionError

PROTOCOL_VERSION = "1.0"


AUTONOMOUS = "autonomous"


@dataclass(slots=True)
class Request:
    """One request, with every field the hosts actually act on.

    These were reachable only by hand-building a dict, which meant the official
    client could not ask for the guarantees the host had been built to provide —
    an agent wanting safe retries had to bypass its own client library to get
    them. Seeds stay in `params`, because they are declared by the tool rather
    than by the protocol.
    """

    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid4()))
    if_revision: int | None = None
    # Names one intended side effect, so a redelivery is recognised rather than
    # repeated. Not a secret and never usable as one.
    idempotency_key: str | None = None
    # Which delivery of that invocation this is. A host with no record of a key
    # cannot tell a retry from a first attempt without being told.
    attempt: int | None = None
    # The editing context this was planned against, so it cannot execute in
    # another one — including on a first delivery, which is not a retry and is
    # exactly as wrong in the wrong world.
    expected_world: str | None = None
    # The unit and axis convention it was planned against; see the host's
    # coordinate contract.
    expected_coordinate_contract: str | None = None
    # `autonomous` requires the whole set above, refused rather than assumed.
    contract: str | None = None
    rv: str = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rv": self.rv, "id": self.id, "method": self.method, "params": self.params}
        if self.if_revision is not None:
            out["if_revision"] = self.if_revision
        for name in ("idempotency_key", "attempt", "expected_world",
                     "expected_coordinate_contract", "contract"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
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
    # `applied` or `noop` on a mutating call: whether the scene actually moved.
    # A successful command that changed nothing is not a change, and the scene
    # revision does not advance for it.
    outcome: str | None = None
    # Which universe the state in this response came from: `authored`, or
    # `play_runtime` in a Unity editor that is playing. The revision is always
    # the authored one, and in `play_runtime` it versions none of what is being
    # reported — the objects are runtime observations, discarded on exit.
    state_domain: str | None = None
    # What this answer's revision is worth: authoritative, notified, independent,
    # or unknown where the failure happened before a method resolved.
    consistency: str | None = None
    # This is a stored result, not a fresh execution.
    replayed: bool | None = None
    # When it is: what the original execution was, stated rather than implied.
    # The envelope describes the host now; a client must never read the revision
    # its retry arrived at as the revision the operation ran at.
    original_execution: dict[str, Any] | None = None
    # Anything this version of the client does not model. A response is still
    # useful when it carries a field newer than the library reading it, and
    # dropping it silently is how a client comes to disagree with its host.
    extra: dict[str, Any] = field(default_factory=dict)
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
        if self.outcome is not None:
            out["outcome"] = self.outcome
        for name in ("state_domain", "consistency", "replayed"):
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        if self.original_execution is not None:
            out["original_execution"] = self.original_execution
        for key, value in self.extra.items():
            out.setdefault(key, value)
        return out

    # Fields this model represents itself; everything else is kept in `extra`.
    MODELLED = (
        "rv", "id", "ok", "revision", "result", "error", "warnings", "timing_ms",
        "outcome", "state_domain", "consistency", "replayed", "original_execution",
    )

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Response":
        if not isinstance(value, dict):
            raise RoboVisionError("INVALID_REQUEST", "response must be an object")
        error = value.get("error")
        return cls(
            id=str(value.get("id", "")),
            ok=bool(value.get("ok")),
            revision=int(value.get("revision", 0)),
            result=value.get("result"),
            error=ErrorPayload(
                code=str(error.get("code", "HOST_ERROR")),
                message=str(error.get("message", "")),
                data=error.get("data"),
                retryable=bool(error.get("retryable")),
            ) if isinstance(error, dict) else None,
            warnings=list(value.get("warnings", []) or []),
            timing_ms=value.get("timing_ms"),
            outcome=value.get("outcome"),
            state_domain=value.get("state_domain"),
            consistency=value.get("consistency"),
            replayed=value.get("replayed"),
            original_execution=value.get("original_execution"),
            extra={key: item for key, item in value.items() if key not in cls.MODELLED},
            rv=str(value.get("rv", PROTOCOL_VERSION)),
        )
