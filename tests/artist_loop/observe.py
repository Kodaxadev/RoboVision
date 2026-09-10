"""Take one measurement and print the discrepancy packet. Nothing else.

The reasoning client's entry point. It receives exactly this — the same
structured evidence any frontier model would get — and nothing about which
defects were applied, what the correct asset looked like, or what the driver
would have chosen. The deterministic advisory ranking is printed alongside so it
can be compared afterwards against what the model actually did; it is never fed
back into the decision.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from observe_support import load_brief  # noqa: E402
from robovision.artist_loop import advisory_ranking, measure, packet  # noqa: E402
from robovision.session import HostSession  # noqa: E402


def main() -> int:
    brief_path = Path(sys.argv[1])
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 9877
    brief = load_brief(brief_path)

    session = HostSession("127.0.0.1", port)
    try:
        health = (session.health().get("result") or {})
        bundle = measure(session, brief)
        evidence = packet(bundle, brief)
        evidence["health"] = {
            "begin_correction": health["ready_for"]["begin_correction"]["status"],
            "mutate": health["ready_for"]["mutate"]["status"],
            "verified_rollback": health["ready_for"]["begin_correction"].get(
                "verified_rollback"),
        }
        evidence["advisory_ranking"] = advisory_ranking(evidence)
        out = brief_path.parent / "packet.json"
        out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
        print(json.dumps(evidence, indent=2, default=str))
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
