"""The change journal, including everything it must refuse to answer.

A journal is only useful if an agent can trust its silence as much as its
events. These assert both: that real changes appear with the right attribution,
and that when the host cannot account for something it says so, keeps saying so,
and refuses to serve a history it no longer has.

The scenarios live in two modules because they answer two different questions.
`journal_events` asks what the host reports and who it blames, `journal_reads`
what a response's revision and cursor are allowed to mean, and `journal_cursors`
what a client's position is allowed to mean.

Headless: nothing here needs a viewport.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import journal_cursors  # noqa: E402
import journal_events  # noqa: E402
import journal_reads  # noqa: E402
from _harness import Host, artifact_dir, run_gate  # noqa: E402


def main() -> None:
    rv = Host("journal")
    scenarios = journal_events.SCENARIOS + journal_reads.SCENARIOS + journal_cursors.SCENARIOS
    for scenario in scenarios:
        scenario(rv)
        print(f"  ok {scenario.__name__}", flush=True)

    (artifact_dir("blender-journal") / "scenarios.txt").write_text(
        "\n".join(
            f"{scenario.__module__}.{scenario.__name__}" for scenario in scenarios
        )
        + "\n",
        encoding="utf-8",
    )


run_gate("BLENDER_JOURNAL", main, "blender-journal")
