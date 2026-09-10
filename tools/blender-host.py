"""Start the RoboVision bridge inside an interactive Blender and stay running.

The Artist Loop's whole claim is that a frontier model drives the editor through
the same public surface a real deployment exposes, so the experiment must not be
a script running inside Blender with privileged access to `bpy`. This launches
the host the way the add-on does — a socket on loopback, serviced by Blender's
own timer — and then gets out of the way. Every subsequent instruction arrives
over that socket from a separate process holding a `HostSession`.

Interactive rather than background, and not for pixels: background Blender's
`ed.undo` reports FINISHED and restores nothing, so a rejected correction could
not be rolled back and the loop's reject branch would be theatre. `system.health`
says so itself by refusing to call `begin_correction` available there.

Usage:
    blender --factory-startup --python tools/blender-host.py -- --port 9877
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
HOST_ROOT = ROOT / "hosts" / "blender"
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))

from robovision_blender.runtime import RUNTIME  # noqa: E402


def _argument(name: str, default: str) -> str:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if name in argv:
        return argv[argv.index(name) + 1]
    return default


def main() -> None:
    port = int(_argument("--port", "9877"))
    # A clean file, so the loop starts from a state the driver established rather
    # than from whatever the startup scene happens to contain.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    RUNTIME.start(port=port)
    print(f"ROBOVISION_HOST_READY port={port} background={bpy.app.background}")
    sys.stdout.flush()


main()
