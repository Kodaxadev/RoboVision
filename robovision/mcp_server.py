from __future__ import annotations

import base64
import io
import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from .client import RoboVisionClient
from .errors import RoboVisionError
from .session import HostSession

HostName = Literal["blender", "unity"]


def _target(host: HostName) -> tuple[str, int]:
    if host == "blender":
        return "127.0.0.1", int(os.environ.get("ROBOVISION_BLENDER_PORT", "9877"))
    if host == "unity":
        return "127.0.0.1", int(os.environ.get("ROBOVISION_UNITY_PORT", "9878"))
    raise ValueError(f"unsupported RoboVision host: {host}")


# One session per host, kept open across tool calls. A client per call orphaned
# every transaction before the next call could use it — measured through this
# exact path — because the connection that opened it closed as the call returned.
_SESSIONS: dict[str, HostSession] = {}
_SESSIONS_LOCK = threading.Lock()


def _session(host: HostName) -> HostSession:
    address, port = _target(host)
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(host)
        if session is None or (session.address, session.port) != (address, port):
            if session is not None:
                session.close()
            session = HostSession(address, port)
            _SESSIONS[host] = session
        return session


def _call(host: HostName, method: str, params: dict[str, Any] | None,
          if_revision: int | None, **fields: Any) -> dict[str, Any]:
    return _session(host).call(method, params or {}, if_revision=if_revision, **fields)


def _model_image(path: str, max_bytes: int = 1_000_000) -> tuple[bytes, str, dict[str, Any]]:
    """Return a model-friendly image while preserving the full artifact on disk."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(path)
    raw = source.read_bytes()
    suffix = source.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg" if suffix in {".jpg", ".jpeg"} else "application/octet-stream"
    if len(raw) <= max_bytes and mime.startswith("image/"):
        return raw, mime, {"transcoded": False, "source_bytes": len(raw), "model_bytes": len(raw)}

    from PIL import Image as PILImage

    with PILImage.open(source) as image:
        image = image.convert("RGB")
        original_size = image.size
        scale = 1.0
        best = b""
        for _ in range(8):
            for quality in (88, 80, 72, 64, 56):
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                best = buffer.getvalue()
                if len(best) <= max_bytes:
                    return best, "image/jpeg", {
                        "transcoded": True,
                        "source_bytes": len(raw),
                        "model_bytes": len(best),
                        "source_size": list(original_size),
                        "model_size": list(image.size),
                        "jpeg_quality": quality,
                    }
            scale *= 0.82
            next_size = (max(64, int(original_size[0] * scale)), max(64, int(original_size[1] * scale)))
            image = image.resize(next_size, PILImage.Resampling.LANCZOS)
        return best, "image/jpeg", {
            "transcoded": True,
            "source_bytes": len(raw),
            "model_bytes": len(best),
            "source_size": list(original_size),
            "model_size": list(image.size),
            "limit_exceeded": len(best) > max_bytes,
        }


def _error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, RoboVisionError):
        return {"code": exc.payload.code, "message": str(exc), "data": exc.payload.data, "retryable": exc.payload.retryable}
    return {"code": type(exc).__name__, "message": str(exc), "retryable": False}


def _preview_path(pass_payload: Any) -> str | None:
    if not isinstance(pass_payload, dict):
        return None
    preview = pass_payload.get("preview")
    if isinstance(preview, dict) and isinstance(preview.get("path"), str):
        return preview["path"]
    if pass_payload.get("kind") == "image" and isinstance(pass_payload.get("path"), str):
        return pass_payload["path"]
    return None


def build_server():
    # Imported lazily so the core RoboVision client has no mandatory MCP dependency.
    from mcp.server import MCPServer
    from mcp.types import CallToolResult, ImageContent, TextContent

    mcp = MCPServer("RoboVision")

    @mcp.tool()
    def rv_status(host: HostName = "blender") -> dict[str, Any]:
        """Discover a connected Blender or Unity RoboVision host.

        Always call this when starting work in an unfamiliar editor session. It
        returns exact editor/version information, scene revision, security facts,
        limitations and the compact live structured operation list. Never assume
        Blender and Unity expose identical capabilities.
        """
        try:
            return _call(host, "system.hello", {}, None)
        except Exception as exc:
            return {"ok": False, "host": host, "error": _error_payload(exc)}

    @mcp.tool()
    def rv_tools(
        host: HostName = "blender",
        query: str = "",
        prefix: str = "",
        tags: list[str] | None = None,
        include_schema: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search the connected host's live structured-operation catalog.

        Prefer this over guessing operation names. Query matches names, summaries
        and tags; prefix narrows to a family such as `mesh.`. Set include_schema
        only when you need parameter contracts for a page of results. Blender
        supports full search/schema paging; older hosts may return a simpler
        unfiltered capability list until they reach protocol parity.
        """
        params = {
            "query": query,
            "prefix": prefix,
            "tags": tags or [],
            "include_schema": include_schema,
            "offset": offset,
            "limit": limit,
        }
        try:
            return _call(host, "system.capabilities", params, None)
        except Exception as exc:
            return {"ok": False, "host": host, "method": "system.capabilities", "error": _error_payload(exc)}

    @mcp.tool()
    def rv_method(host: HostName, method: str) -> dict[str, Any]:
        """Get the exact parameter schema and safety metadata for one host method.

        Use this before a complex or unfamiliar edit. If a host has not yet
        implemented schema discovery the returned structured error makes that
        limitation explicit rather than encouraging argument guessing.
        """
        try:
            return _call(host, "system.method", {"method": method}, None)
        except Exception as exc:
            return {"ok": False, "host": host, "method": method, "error": _error_payload(exc)}

    @mcp.tool()
    def rv_call(
        host: HostName,
        method: str,
        params: dict[str, Any] | None = None,
        if_revision: int | None = None,
        idempotency_key: str | None = None,
        attempt: int | None = None,
        expected_world: str | None = None,
        expected_coordinate_contract: str | None = None,
        contract: str | None = None,
    ) -> dict[str, Any]:
        """Call one structured RoboVision editor operation.

        For unattended work pass `contract="autonomous"` with `expected_world`,
        `if_revision`, `idempotency_key` and `attempt`. The host then refuses the
        call rather than executing it if any of that is missing, a retry cannot
        be executed twice, and a request planned against one editing context
        cannot run in another — including on a first delivery, which is not a
        retry and is exactly as wrong in the wrong world.

        Use rv_status/rv_tools first to discover supported methods and rv_method
        for an exact schema when needed. For mutations, pass the most recently
        observed scene revision as if_revision whenever possible. For visual
        capture use rv_capture/rv_perception so the model receives pixels, not
        only artifact paths.
        """
        try:
            return _call(host, method, params, if_revision,
                         idempotency_key=idempotency_key, attempt=attempt,
                         expected_world=expected_world,
                         expected_coordinate_contract=expected_coordinate_contract,
                         contract=contract)
        except Exception as exc:
            return {"ok": False, "host": host, "method": method, "error": _error_payload(exc)}

    @mcp.tool(structured_output=False)
    def rv_perception(
        host: HostName = "blender",
        params: dict[str, Any] | None = None,
        model_passes: list[str] | None = None,
        max_model_bytes_per_image: int = 600_000,
    ):
        """Capture an aligned multi-pass perception bundle and return labeled pixels.

        Blender currently supports color, solid, wireframe, world normals,
        object IDs, material IDs and float depth, all with one view/projection.
        Full-resolution/raw artifacts remain on disk. model_passes controls which
        viewable previews are placed into model context; it does not remove host
        artifacts. Defaults to solid, normals, object_ids and depth.
        """
        desired = model_passes or ["solid", "normals", "object_ids", "depth"]
        if any(not isinstance(item, str) for item in desired):
            error = {"ok": False, "host": host, "method": "perception.capture_bundle", "error": {"code": "INVALID_PARAMS", "message": "model_passes must contain strings", "retryable": False}}
            return CallToolResult(is_error=True, content=[TextContent(text=json.dumps(error))], structured_content=error)
        try:
            response = _call(host, "perception.capture_bundle", params or {}, None)
            structured = dict(response)
            host_passes = response.get("result", {}).get("passes", {})
            model_meta: dict[str, Any] = {}
            content = [TextContent(text=json.dumps(structured, ensure_ascii=False, separators=(",", ":")))]
            for pass_name in desired:
                path = _preview_path(host_passes.get(pass_name))
                if path is None:
                    continue
                image_bytes, mime, transport_meta = _model_image(
                    path,
                    max(100_000, min(int(max_model_bytes_per_image), 4_000_000)),
                )
                model_meta[pass_name] = transport_meta
                content.append(TextContent(text=f"RoboVision perception pass: {pass_name}"))
                content.append(ImageContent(data=base64.b64encode(image_bytes).decode("ascii"), mime_type=mime))
            structured["model_images"] = model_meta
            return CallToolResult(content=content, structured_content=structured)
        except Exception as exc:
            error = {"ok": False, "host": host, "method": "perception.capture_bundle", "error": _error_payload(exc)}
            return CallToolResult(
                is_error=True,
                content=[TextContent(text=json.dumps(error, ensure_ascii=False))],
                structured_content=error,
            )

    @mcp.tool(structured_output=False)
    def rv_capture(
        host: HostName,
        params: dict[str, Any] | None = None,
        if_revision: int | None = None,
        max_model_bytes: int = 1_000_000,
    ):
        """Capture the editor view and return provenance plus actual image pixels.

        The editor host records projection/camera provenance with the artifact.
        RoboVision preserves the full-resolution local artifact and only
        transcodes a model-facing copy when needed for conservative client limits.
        """
        capture_params = dict(params or {})
        if host == "unity":
            capture_params.setdefault("width", 1280)
            capture_params.setdefault("height", 720)
        try:
            response = _call(host, "viewport.capture", capture_params, if_revision)
            artifact = response.get("result", {}).get("artifact", {})
            path = artifact.get("path")
            if not isinstance(path, str) or not path:
                raise RuntimeError("host capture did not return an image artifact path")
            image_bytes, mime, transport_meta = _model_image(
                path,
                max(100_000, min(int(max_model_bytes), 8_000_000)),
            )
            structured = dict(response)
            structured["model_image"] = transport_meta
            summary = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
            return CallToolResult(
                content=[
                    TextContent(text=summary),
                    ImageContent(data=base64.b64encode(image_bytes).decode("ascii"), mime_type=mime),
                ],
                structured_content=structured,
            )
        except Exception as exc:
            error = {"ok": False, "host": host, "method": "viewport.capture", "error": _error_payload(exc)}
            return CallToolResult(
                is_error=True,
                content=[TextContent(text=json.dumps(error, ensure_ascii=False))],
                structured_content=error,
            )

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
