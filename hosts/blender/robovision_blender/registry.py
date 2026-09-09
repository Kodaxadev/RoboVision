from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .tool_docs import METHOD_DOCS

# What a call guarantees about the scene state behind its answer.
#
# This is a separate axis from `evidence`, which says whether a call produces a
# durable artifact. Only the two capture methods are evidence-producing, while
# `scene.describe` produces no artifact and still must not answer from a stale
# baseline, so the two cannot be the same flag.
#
# AUTHORITATIVE  the host re-reads the scene before answering, so the state, the
#                scene revision and the journal position in one response all
#                describe the same moment
# NOTIFIED       the answer reflects the last editor notification received.
#                Cheap on purpose, and it may lag a change the editor never
#                announced; the response says so rather than implying freshness
# INDEPENDENT    the answer does not depend on scene state at all
AUTHORITATIVE = "authoritative"
NOTIFIED = "notified"
INDEPENDENT = "independent"
# No tool was resolved, so no class of answer applies. Reported on failures that
# happen before a method is known — a protocol mismatch, a missing id, an unknown
# method. Claiming `independent` there would be almost right and occasionally
# wrong; this says what is actually the case.
UNKNOWN = "unknown"
READ_CONSISTENCY = (AUTHORITATIVE, NOTIFIED, INDEPENDENT)

# How reproducible an operation is, which is a different question from whether it
# takes a seed. A seed makes a stochastic computation repeatable; it does not by
# itself make the result reproducible, because the thing consuming it may not be
# under our control. The proof is ultimately the output hash, and a recipe must
# never claim a determinism its backend does not provide.
#
# EXACT                same inputs, same tool version, same canonical output
# SEEDED               a seed is required and expected to reproduce the result
# EXTERNAL_OR_UNPROVEN seeds and versions are recorded, bit-identical output is
#                      not guaranteed by the system actually doing the work
EXACT = "exact"
SEEDED = "seeded"
EXTERNAL_OR_UNPROVEN = "external_or_unproven"
DETERMINISM = (EXACT, SEEDED, EXTERNAL_OR_UNPROVEN)


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
    # Authoritative by default: a tool that presents scene state and forgets to
    # classify itself should be correct and slow, never fast and wrong.
    reads: str = AUTHORITATIVE
    stability: str = "alpha"
    summary: str = ""
    tags: tuple[str, ...] = ()
    params_schema: dict[str, Any] | None = None
    # Named randomness channels this tool consumes. Named rather than a single
    # seed because the useful precedent separates them: a text-to-reference-to-3D
    # pipeline wants its image, geometry and texture branches independently
    # reproducible, and one seed for all three makes "same shape, different
    # texture" impossible to ask for.
    seeds: tuple[str, ...] = ()
    determinism: str = EXACT

    def describe(self, *, include_schema: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "mutating": self.mutating,
            "evidence": self.evidence,
            "requires_ui": self.requires_ui,
            "reads": self.reads,
            "determinism": self.determinism,
            "seeds": list(self.seeds),
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
        reads: str = AUTHORITATIVE,
        stability: str = "alpha",
        summary: str | None = None,
        tags: Iterable[str] | None = None,
        params_schema: dict[str, Any] | None = None,
        seeds: Iterable[str] | None = None,
        determinism: str = EXACT,
    ) -> None:
        if name in self._tools:
            raise RuntimeError(f"duplicate RoboVision tool: {name}")
        if reads not in READ_CONSISTENCY:
            raise RuntimeError(f"{name}: reads must be one of {READ_CONSISTENCY}")
        if mutating and reads != AUTHORITATIVE:
            raise RuntimeError(f"{name}: a mutating tool re-reads before it runs; it cannot be {reads}")
        resolved_seeds = tuple(str(channel) for channel in (seeds or ()))
        if determinism not in DETERMINISM:
            raise RuntimeError(f"{name}: determinism must be one of {DETERMINISM}")
        # The two halves of the declaration have to agree, or the metadata is
        # decoration. A seeded tool with no channels cannot be reproduced, and
        # channels on a tool claiming exactness are randomness nobody records.
        if determinism == SEEDED and not resolved_seeds:
            raise RuntimeError(f"{name}: a seeded tool must name the randomness channels it consumes")
        if resolved_seeds and determinism == EXACT:
            raise RuntimeError(
                f"{name}: a tool that consumes seeds is not exact; declare seeded or "
                f"external_or_unproven")
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
            reads,
            stability,
            resolved_summary,
            resolved_tags,
            resolved_schema,
            resolved_seeds,
            determinism,
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
