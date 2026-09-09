"""Blender across a real process boundary.

Reopening a document inside one process is not the same event as starting
Blender again: the interpreter is gone, every module-level registry is gone, the
socket is reclaimed by the OS rather than by our own shutdown, and only what was
written into the `.blend` survives. Those are separate guarantees, so they are
proven separately.

ROBOVISION_RESTART_PHASE selects the half. Phase 1 builds and records; phase 2
runs in a second process and checks the contract against that record.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import Host, expect  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get("ROBOVISION_RESTART_STATE", "robovision-blender-restart.json"))
DOCUMENT = Path(os.environ.get("ROBOVISION_RESTART_DOCUMENT", "robovision-restart.blend"))


def free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def clean_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def phase_one() -> None:
    clean_scene()
    rv = Host("restart1")

    alpha = rv.result("object.create", {"kind": "cube", "name": "RestartAlpha", "location": [1.0, 2.0, 3.0]})["id"]
    beta = rv.result("object.create", {"kind": "cube", "name": "RestartBeta"})["id"]
    mesh_revision = rv.result("mesh.inspect", {"object": alpha})["mesh_revision"]

    DOCUMENT.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(DOCUMENT))

    described = rv.result("scene.describe")
    port = free_port()
    rv.runtime.start(port=port)

    STATE.write_text(
        json.dumps(
            {
                "phase1": "ok",
                "blender": bpy.app.version_string,
                "document": str(DOCUMENT),
                "objects": {"RestartAlpha": alpha, "RestartBeta": beta},
                "mesh_revision": mesh_revision,
                "fingerprint": rv.fingerprint(),
                "bridge": described["bridge"],
                "world_incarnation": described["world_incarnation"],
                "revision": described["revision"],
                "port": port,
                "listening": rv.runtime.running,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("ROBOVISION_BLENDER_RESTART_PHASE1_OK", flush=True)


def phase_two() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    expect(state.get("phase1") == "ok", f"phase 1 did not succeed: {state}")

    bpy.ops.wm.open_mainfile(filepath=state["document"])
    rv = Host("restart2")
    described = rv.result("scene.describe")

    # 1. Only what the document carries may survive.
    for name, durable in state["objects"].items():
        resolved = rv.result("object.inspect", {"object": durable})
        expect(resolved["name"] == name, f"{durable} no longer resolves to {name} after a restart")
    expect(
        rv.result("mesh.inspect", {"object": state["objects"]["RestartAlpha"]})["mesh_revision"]
        == state["mesh_revision"],
        "the mesh revision did not survive a restart",
    )
    expect(
        rv.fingerprint() == state["fingerprint"],
        "the scene fingerprint changed across a restart of the same document",
    )

    # 2. Runtime-scoped state must not appear to have survived.
    expect(
        described["bridge"] != state["bridge"],
        "a new process reported the previous bridge identity",
    )
    expect(
        described["world_incarnation"] != state["world_incarnation"],
        "a new process reported the previous document incarnation",
    )
    expect(described["revision"] == 0, f"the revision must reset in a new process, got {described['revision']}")
    expect(
        rv.runtime.transactions.active is None,
        "a transaction appeared to survive a process restart",
    )

    # 3. The port the dead process held must be free and bindable.
    port = int(state["port"])
    rv.runtime.start(port=port)
    expect(rv.runtime.running, "the new process could not bind the port the old one held")

    # 4. The shipped Python client must be able to drive this process.
    script = REPO / "tests" / "blender" / "_restart_client.py"
    script.write_text(
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from robovision.client import RoboVisionClient\n"
        "with RoboVisionClient('127.0.0.1', int(sys.argv[2]), timeout=30.0) as c:\n"
        "    hello = c.call('system.hello')\n"
        "    described = c.call('scene.describe')\n"
        "    print('RESULT ' + json.dumps({\n"
        "        'editor': hello['result']['editor']['name'],\n"
        "        'bridge': hello['result']['bridge'],\n"
        "        'objects': sorted(o['name'] for o in described['result']['objects']),\n"
        "    }))\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [os.environ.get("ROBOVISION_PYTHON", "python"), str(script), str(REPO), str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Background Blender never ticks its timers while this script runs, so the
    # transport is pumped here or the external client would wait forever.
    deadline = time.time() + 60
    while process.poll() is None and time.time() < deadline:
        rv.runtime.transport.poll(rv.runtime.dispatch, command_budget=8)
        time.sleep(0.005)
    stdout, stderr = process.communicate(timeout=30)
    expect(process.returncode == 0, f"the python client failed ({process.returncode}): {stderr}")
    marker = stdout.find("RESULT ")
    expect(marker >= 0, f"no result from the python client: {stdout} {stderr}")
    payload = json.loads(stdout[marker + len("RESULT ") :].strip())

    expect(payload["editor"] == "Blender", f"unexpected editor over the wire: {payload}")
    expect(payload["bridge"] == described["bridge"], "the client saw a different bridge identity")
    expect(payload["objects"] == ["RestartAlpha", "RestartBeta"], f"the client saw {payload['objects']}")
    script.unlink(missing_ok=True)

    print("ROBOVISION_BLENDER_RESTART_PHASE2_OK", flush=True)


phase = os.environ.get("ROBOVISION_RESTART_PHASE", "1")
try:
    if phase == "1":
        phase_one()
    else:
        phase_two()
except Exception:
    import traceback

    traceback.print_exc()
    sys.exit(1)
