"""Execute a declared initial candidate through the public path, and record it.

A0 is the reasoning client's own first attempt at the brief. It is built with the
same typed operations a correction uses and over the same session, so nothing
about the starting asset depends on private access — and the file that produced
it records which model chose the massing and why.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from robovision.session import HostSession  # noqa: E402


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    brief_path = Path(sys.argv[2])
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 9877

    session = HostSession("127.0.0.1", port)
    try:
        described = session.call("scene.describe")["result"]
        for obj in described["objects"]:
            session.call("object.delete", {"object": obj["name"]})
        for entry in spec["objects"]:
            session.call("object.create",
                         {"kind": "cube", "name": entry["name"], "size": 1.0})
            session.call("object.transform",
                         {"object": entry["name"], "scale": entry["scale"],
                          "location": entry["location"]})
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
        brief["subjects"] = [entry["name"] for entry in spec["objects"]]
        brief["a0_source"] = spec["source"]
        brief["a0_reasoning"] = spec["reasoning"]
        brief_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
        print(f"A0_BUILT objects={len(spec['objects'])}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
