from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .tool_docs import METHOD_DOCS


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
    summary: str = ""
    tags: tuple[str, ...] = ()
    params_schema: dict[str, Any] | None = None

    def describe(self, *, include_schema: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "mutating": self.mutating,
            "evidence": self.evidence,
            "requires_ui": self.requires_ui,
            "stability": self.stability,
            "summary": self.summary,
            "tags": list(self.tags),
        }
        if include_schema:
            result["params_schema"] = self.params_schema or {
                "type": "object",
                "additionalProperties": True,
                "description": "This method has not yet published a strict parameter schema.",
            }
        return result


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
        summary: str | None = None,
        tags: Iterable[str] | None = None,
        params_schema: dict[str, Any] | None = None,
    ) -> None:
        if name in self._tools:
            raise RuntimeError(f"duplicate RoboVision tool: {name}")
        documented = METHOD_DOCS.get(name, {})
        resolved_summary = str(summary if summary is not None else documented.get("summary", ""))
        resolved_tags = tuple(str(tag) for tag in (tags if tags is not None else documented.get("tags", ())))
        resolved_schema = params_schema if params_schema is not None else documented.get("params_schema")
        self._tools[name] = ToolSpec(
            name,
            handler,
            mutating,
            evidence,
            requires_ui,
            stability,
            resolved_summary,
            resolved_tags,
            resolved_schema,
        )

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise HostError("UNKNOWN_METHOD", f"unknown method: {name}") from exc

    def capabilities(self, *, include_schema: bool = False) -> list[dict[str, Any]]:
        return [self._tools[name].describe(include_schema=include_schema) for name in sorted(self._tools)]

    def catalog(
        self,
        *,
        query: str = "",
        prefix: str = "",
        tags: Iterable[str] = (),
        include_schema: bool = False,
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        query_cf = query.casefold().strip()
        prefix_cf = prefix.casefold().strip()
        required_tags = {str(tag).casefold() for tag in tags}
        matches: list[ToolSpec] = []
        for name in sorted(self._tools):
            spec = self._tools[name]
            if prefix_cf and not spec.name.casefold().startswith(prefix_cf):
                continue
            spec_tags = {tag.casefold() for tag in spec.tags}
            if required_tags and not required_tags.issubset(spec_tags):
                continue
            if query_cf:
                haystack = " ".join((spec.name, spec.summary, *spec.tags)).casefold()
                if query_cf not in haystack:
                    continue
            matches.append(spec)

        safe_offset = max(0, int(offset))
        safe_limit = max(1, min(500, int(limit)))
        page = matches[safe_offset : safe_offset + safe_limit]
        return {
            "total": len(matches),
            "offset": safe_offset,
            "limit": safe_limit,
            "has_more": safe_offset + len(page) < len(matches),
            "methods": [spec.describe(include_schema=include_schema) for spec in page],
        }
