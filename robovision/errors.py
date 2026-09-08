from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ErrorPayload:
    code: str
    message: str
    data: Any = None
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.data is not None:
            out["data"] = self.data
        return out


class RoboVisionError(RuntimeError):
    def __init__(self, code: str, message: str, *, data: Any = None, retryable: bool = False):
        super().__init__(message)
        self.payload = ErrorPayload(code, message, data, retryable)
