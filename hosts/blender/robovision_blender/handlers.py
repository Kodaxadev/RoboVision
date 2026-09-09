"""Blender's change notifications, and keeping exactly one set of them registered.

Blender handlers are module-level functions, so a notification fans out to every
runtime that asked for it. The add-on uses one; an integration harness may build
its own instance and must see the same notifications.

The reload problem this module exists to solve: an add-on disable/enable builds
a fresh copy of every function in the package, so a callback registered by the
previous load is a different object and an identity check cannot see it. Without
that, `load_pre`, `load_post` and `save_post` accumulated one dead `@persistent`
callback per cycle, each holding its whole module alive. Every callback is
therefore tagged with the token of the load that created it, and a load sweeps
out anything tagged by a different one.
"""
from __future__ import annotations

import uuid

import bpy
from bpy.app.handlers import persistent

ACTIVE_RUNTIMES: "set" = set()

# One token per load of this module.
MODULE_TOKEN = str(uuid.uuid4())


@persistent
def _depsgraph_dirty(_scene=None, _depsgraph=None) -> None:
    for runtime in tuple(ACTIVE_RUNTIMES):
        runtime.mark_dirty()


@persistent
def _document_closing(_file=None, _other=None) -> None:
    for runtime in tuple(ACTIVE_RUNTIMES):
        runtime.document_closing()


@persistent
def _document_opened(_file=None, _other=None) -> None:
    for runtime in tuple(ACTIVE_RUNTIMES):
        runtime.document_opened()


@persistent
def _document_saved(_file=None, _other=None) -> None:
    for runtime in tuple(ACTIVE_RUNTIMES):
        runtime.document_saved()


for _handler in (_depsgraph_dirty, _document_closing, _document_opened, _document_saved):
    _handler._robovision_token = MODULE_TOKEN


def slots():
    """Each Blender handler list paired with the callback we put in it."""
    return (
        (bpy.app.handlers.depsgraph_update_post, _depsgraph_dirty),
        (bpy.app.handlers.load_pre, _document_closing),
        (bpy.app.handlers.load_post, _document_opened),
        (bpy.app.handlers.save_post, _document_saved),
    )


def drop_foreign_incarnations() -> int:
    """Remove callbacks left behind by a previous load of this package.

    Only functions this package tagged are considered, so another add-on's
    handlers are never touched.
    """
    removed = 0
    for handler_list, _ in slots():
        for handler in list(handler_list):
            token = getattr(handler, "_robovision_token", None)
            if token is not None and token != MODULE_TOKEN:
                handler_list.remove(handler)
                removed += 1
    return removed


def attach(runtime) -> bool:
    """Register the notifications and enrol `runtime`.

    Returns True when this was a genuine attach rather than a repeat, which is
    what tells the runtime it is a new bridge incarnation.
    """
    attaching = runtime not in ACTIVE_RUNTIMES
    ACTIVE_RUNTIMES.add(runtime)
    drop_foreign_incarnations()
    for handler_list, handler in slots():
        if handler not in handler_list:
            handler_list.append(handler)
    return attaching


def detach(runtime) -> None:
    """Withdraw `runtime`, and unregister the callbacks once nobody is left.

    All four, not just the depsgraph one. The load and save handlers are
    `@persistent`, so leaving them registered outlives the add-on that installed
    them.
    """
    ACTIVE_RUNTIMES.discard(runtime)
    if ACTIVE_RUNTIMES:
        return
    for handler_list, handler in slots():
        while handler in handler_list:
            handler_list.remove(handler)


def registered_counts() -> dict[str, int]:
    """How many of our callbacks each list currently holds. Evidence, not state."""
    return {
        name: len([h for h in getattr(bpy.app.handlers, name)
                   if getattr(h, "_robovision_token", None) is not None])
        for name in ("depsgraph_update_post", "load_pre", "load_post", "save_post")
    }
