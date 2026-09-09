"""The bridge's own lifecycle: detach, reattach, and a genuine add-on reload.

These are three different events and were being conflated. Calling
`remove_handlers()` then `install_handlers()` on a live runtime object proves
detach and reattach — the module stays loaded, every function object survives,
and the class the runtime is an instance of is the same class. An add-on
disable and re-enable is a stronger thing: Blender runs `unregister`, the module
is purged, and a re-enable builds an entirely new set of function objects.

The distinction matters because it is exactly where handler cleanup fails. A
`@persistent` callback registered by the previous load is a different object
from the new module's callback, so the identity check that guards installation
cannot see it and registers alongside it. Measured before this gate existed: an
enable/disable/enable cycle left `load_pre` at 3, `load_post` at 4 and
`save_post` at 2, one dead callback per list per cycle, each holding its whole
module alive. `remove_handlers()` was removing only the depsgraph handler.

Headless: none of it needs a viewport.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, expect, run_gate  # noqa: E402

LISTS = ("depsgraph_update_post", "load_pre", "load_post", "save_post")


def ours() -> dict[str, list[str]]:
    """The tokens of every RoboVision callback currently registered.

    Counting is not enough: after a reload the counts can look right while the
    callbacks belong to a module that no longer exists. The token says which
    load put each one there.
    """
    return {
        name: [
            token
            for token in (
                getattr(handler, "_robovision_token", None)
                for handler in getattr(bpy.app.handlers, name)
            )
            if token is not None
        ]
        for name in LISTS
    }


def totals() -> dict[str, int]:
    return {name: len(getattr(bpy.app.handlers, name)) for name in LISTS}


def expect_exactly_one_load(where: str) -> str:
    registered = ours()
    for name, tokens in registered.items():
        expect(len(tokens) == 1, f"{where}: {name} holds {len(tokens)} RoboVision callbacks: {tokens}")
    tokens = {tokens[0] for tokens in registered.values()}
    expect(len(tokens) == 1, f"{where}: callbacks from more than one load are registered: {registered}")
    return tokens.pop()


def detach_and_reattach_repeatedly(rv: Host) -> None:
    """What add-on disable/enable does to a runtime, and no more than that.

    This proves detach and reattach. It deliberately does not claim to prove an
    add-on reload, which is a different event and is exercised below.
    """
    before = expect_exactly_one_load("attached")
    cursor = rv.result("scene.changes_since")["cursor"]

    bridges, documents = [], []
    for cycle in range(3):
        rv.runtime.remove_handlers()
        expect(
            all(not tokens for tokens in ours().values()),
            f"detaching the last runtime left callbacks registered: {ours()}",
        )
        rv.runtime.install_handlers()
        token = expect_exactly_one_load(f"reattach {cycle}")
        expect(token == before, "reattaching within one module load changed the module token")
        described = rv.result("scene.describe")
        bridges.append(described["bridge"])
        documents.append(described["document_incarnation"])

    expect(len(set(bridges)) == 3, f"reattaching reused a bridge identity: {bridges}")
    expect(
        len(set(documents)) == 3,
        f"a reattached bridge cannot know what the previous one minted, so the document "
        f"incarnation must rotate too: {documents}",
    )

    # A cursor from before the reattach names a world this bridge never knew.
    rv.call("scene.changes_since", {"cursor": cursor}, ok=False, code="STALE_DOCUMENT")
    restarted = rv.result("scene.changes_since")
    expect(restarted["epoch"] == 1, f"the journal did not restart on reattach: {restarted['epoch']}")


def a_leftover_callback_is_swept(rv: Host) -> None:
    """A load whose `unregister` never ran.

    A crash, or a runtime some script built and never stopped, leaves callbacks
    registered that the next load cannot see by identity. They are tagged, so
    they can be recognised — and only they can, which is the other half of the
    guarantee: another add-on's handlers must survive untouched.
    """
    def leftover(*_args) -> None:
        raise AssertionError("a callback from a previous add-on load was still invoked")

    leftover._robovision_token = "a-previous-load"
    bpy.app.handlers.load_post.append(leftover)

    def another_addon(*_args) -> None:
        pass

    bpy.app.handlers.load_post.append(another_addon)

    rv.runtime.remove_handlers()
    rv.runtime.install_handlers()

    expect(leftover not in bpy.app.handlers.load_post,
           "a callback tagged by a previous load survived a fresh install")
    expect(another_addon in bpy.app.handlers.load_post,
           "the sweep removed a handler that does not belong to RoboVision")
    expect_exactly_one_load("after the sweep")
    bpy.app.handlers.load_post.remove(another_addon)


def the_real_addon_lifecycle(rv: Host) -> None:
    """Enable, disable, purge, enable again — the shipped register/unregister.

    `install_handlers()` stands in for the Start operator, which is the only
    thing that installs them; `register()` alone never has. Everything that
    removes them is the shipped path: `disable` runs the add-on's `unregister`,
    which stops the runtime, which detaches.
    """
    import addon_utils

    # Leave the harness's own runtime out of it: while it is attached, detaching
    # the add-on's runtime correctly declines to unregister the shared
    # callbacks, and this scenario is about what happens when nobody is left.
    rv.runtime.remove_handlers()
    baseline = totals()
    expect(all(not tokens for tokens in ours().values()),
           f"the process did not start this scenario clean: {ours()}")

    enabled = addon_utils.enable("robovision_blender", default_set=False, persistent=False)
    expect(enabled is not None, "the add-on did not enable")
    import robovision_blender.runtime as first_load

    first_load.RUNTIME.install_handlers()
    first_token = expect_exactly_one_load("after enable")

    addon_utils.disable("robovision_blender", default_set=False)
    expect(
        all(not tokens for tokens in ours().values()),
        f"the add-on's own unregister left callbacks registered: {ours()}",
    )
    expect(totals() == baseline, f"disabling did not return the handler lists to {baseline}: {totals()}")

    # A re-enable after a purge is what actually reloads the module, and is
    # where the accumulation used to happen.
    for name in [module for module in list(sys.modules) if module.startswith("robovision_blender")]:
        del sys.modules[name]
    addon_utils.modules_refresh()
    again = addon_utils.enable("robovision_blender", default_set=False, persistent=False)
    expect(again is not None, "the add-on did not re-enable after a reload")

    import robovision_blender.runtime as second_load

    expect(
        first_load is not second_load,
        "the module was not actually reloaded, so this proves nothing about a reload",
    )
    second_load.RUNTIME.install_handlers()
    second_token = expect_exactly_one_load("after reload and re-enable")
    expect(second_token != first_token, "the reloaded module reused the previous load's token")
    expect(
        totals() == {name: count + 1 for name, count in baseline.items()},
        f"a reload accumulated handlers: {baseline} became {totals()}",
    )

    second_load.RUNTIME.remove_handlers()
    expect(totals() == baseline, "the reloaded add-on did not clean up after itself")
    return {"first_token": first_token, "second_token": second_token, "baseline": baseline}


def main() -> None:
    rv = Host("addon-lifecycle")
    detach_and_reattach_repeatedly(rv)
    print("  ok detach_and_reattach_repeatedly", flush=True)
    a_leftover_callback_is_swept(rv)
    print("  ok a_leftover_callback_is_swept", flush=True)
    summary = the_real_addon_lifecycle(rv)
    print("  ok the_real_addon_lifecycle", flush=True)

    (artifact_dir("blender-addon-lifecycle") / "summary.txt").write_text(
        "\n".join(
            [
                f"blender {bpy.app.version_string}",
                f"handler_baseline {summary['baseline']}",
                f"first_load_token {summary['first_token']}",
                f"reloaded_token {summary['second_token']}",
                "detach_reattach proven; add-on disable/reload/enable proven",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


run_gate("BLENDER_ADDON_LIFECYCLE", main, "blender-addon-lifecycle")
