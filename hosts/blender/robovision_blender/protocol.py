"""Wire constants, kept where both the runtime and the dispatcher can see them.

They live here rather than in `runtime` so the dispatch loop can import them
without importing the runtime that calls it.
"""
from __future__ import annotations

PROTOCOL_VERSION = "1.0"
HOST_VERSION = "0.1.0"

# Which universe the state in a response came from. Blender only has one: there
# is no play mode instantiating a copy of the scene and discarding it again, so
# every response here is about authored state. The field is still reported, so a
# client driving both editors reads the same field in both.
AUTHORED = "authored"
