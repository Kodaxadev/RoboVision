from __future__ import annotations

import bpy

from ..registry import INDEPENDENT, NOTIFIED, HostError
from ..protocol import HOST_VERSION, PROTOCOL_VERSION


def ping(_params, runtime):
    return {"pong": True, "host": "blender", "revision": runtime.revision}


def capabilities(params, runtime):
    query = params.get("query", "")
    prefix = params.get("prefix", "")
    tags = params.get("tags", [])
    if not isinstance(query, str) or not isinstance(prefix, str):
        raise HostError("INVALID_PARAMS", "query and prefix must be strings")
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise HostError("INVALID_PARAMS", "tags must be an array of strings")
    try:
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 100))
    except (TypeError, ValueError) as exc:
        raise HostError("INVALID_PARAMS", "offset and limit must be integers") from exc
    return runtime.registry.catalog(
        query=query,
        prefix=prefix,
        tags=tags,
        include_schema=bool(params.get("include_schema", False)),
        offset=offset,
        limit=limit,
    )


def method(params, runtime):
    name = params.get("method")
    if not isinstance(name, str) or not name:
        raise HostError("INVALID_PARAMS", "method is required")
    return runtime.registry.get(name).describe(include_schema=True)


def hello(_params, runtime):
    live_capabilities = runtime.registry.capabilities(include_schema=False)
    return {
        "protocol": PROTOCOL_VERSION,
        "host": {"name": "blender", "implementation": "robovision_blender", "version": HOST_VERSION},
        "editor": {
            "name": "Blender",
            "version": bpy.app.version_string,
            "version_tuple": list(bpy.app.version),
            "background": bool(bpy.app.background),
        },
        "revision": runtime.revision,
        "bridge": runtime.bridge,
        "journal": runtime.journal.state(),
        "world_incarnation": runtime.world_incarnation,
        "document": runtime.document,
        # Enough to diagnose an interrupted transaction — who holds it, what
        # state it is in, whether it needs adopting and whether a verified
        # rollback is available — and nothing that would let a caller take it.
        "transaction": {
            "active": runtime.transactions.active is not None,
            "state": runtime.transactions.state(),
        },
        "capability_count": len(live_capabilities),
        "capabilities": live_capabilities,
        "discovery": {
            "search": "system.capabilities",
            "describe_exact": "system.method",
            "schemas_on_demand": True,
        },
        "transport": {
            "kind": "tcp-jsonl",
            "bind": runtime.transport.host if runtime.transport else "127.0.0.1",
            "port": runtime.transport.port if runtime.transport else None,
            "editor_thread_dispatch": True,
            "python_worker_threads": False,
        },
        "security": {"loopback_only": True, "arbitrary_code_enabled": False},
        "limitations": [
            "Blender Global Undo must be enabled for verified transactions.",
            "Viewport evidence methods require an open interactive VIEW_3D area.",
            "Mesh element indices are valid only for the returned mesh revision.",
        ],
    }


def register(registry) -> None:
    # ping and hello report the scene revision and the journal position without
    # reporting any scene state, so they cannot pair current geometry with a
    # stale revision. They stay cheap and say so; a client that needs a proven
    # revision takes a snapshot.
    registry.add("system.ping", ping, reads=NOTIFIED, stability="beta")
    registry.add("system.hello", hello, reads=NOTIFIED, stability="beta")
    # Pure protocol metadata: the catalog does not depend on the scene at all.
    registry.add("system.capabilities", capabilities, reads=INDEPENDENT, stability="beta")
    registry.add("system.method", method, reads=INDEPENDENT, stability="beta")
