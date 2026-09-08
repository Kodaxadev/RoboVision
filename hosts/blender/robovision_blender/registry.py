from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class HostError(RuntimeError):
    def __init__(self, code: str, message: str, *, data: Any = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.data = data
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    handler: Callable[[dict[str, Any], Any], Any]
    mutating: bool = False
    evidence: bool = False
    requires_ui: bool = False
    stability: str = "alpha"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mutating": self.mutating,
            "evidence": self.evidence,
            "requires_ui": self.requires_ui,
            "stability": self.stability,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def add(
        self,
        name: str,
        handler: Callable[[dict[str, Any], Any], Any],
        *,
        mutating: bool = False,
        evidence: bool = False,
        requires_ui: bool = False,
        stability: str = "alpha",
    ) -> None:
        if name in self._tools:
            raise RuntimeError(f"duplicate RoboVision tool: {name}")
        self._tools[name] = ToolSpec(name, handler, mutating, evidence, requires_ui, stability)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise HostError("UNKNOWN_METHOD", f"unknown method: {name}") from exc

    def capabilities(self) -> list[dict[str, Any]]:
        return [self._tools[name].describe() for name in sorted(self._tools)]
