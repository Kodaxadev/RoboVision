from __future__ import annotations

import bpy

from ..runtime import HOST_VERSION, PROTOCOL_VERSION


def ping(_params, runtime):
    return {"pong": True, "host": "blender", "revision": runtime.revision}


def capabilities(_params, runtime):
    return {"methods": runtime.registry.capabilities()}


def hello(_params, runtime):
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
        "capabilities": runtime.registry.capabilities(),
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
    registry.add("system.ping", ping, stability="beta")
    registry.add("system.hello", hello, stability="beta")
    registry.add("system.capabilities", capabilities, stability="beta")
