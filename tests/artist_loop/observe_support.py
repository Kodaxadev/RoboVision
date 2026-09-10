"""Reading a brief, shared by the observation and attempt entry points.

Kept in one place so the evidence a client reads and the declaration a
correction is judged against cannot drift into two slightly different briefs.
"""
from __future__ import annotations

import json
from pathlib import Path

from robovision.artist_loop import Brief


def load_brief(path: Path) -> Brief:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return Brief(
        name=raw["name"], subjects=raw["subjects"], references=raw["references"],
        patterns=raw["patterns"], protected_objects=raw["protected_objects"],
        required_invariants=raw["required_invariants"],
        max_dimension=raw.get("max_dimension"), notes=raw.get("notes", ""))
