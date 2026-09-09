"""Wire constants, kept where both the runtime and the dispatcher can see them.

They live here rather than in `runtime` so the dispatch loop can import them
without importing the runtime that calls it.
"""
from __future__ import annotations

PROTOCOL_VERSION = "1.0"
HOST_VERSION = "0.1.0"
