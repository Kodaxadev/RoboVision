"""An append-only record of what was attempted, written before it is attempted.

One post-operation entry would be enough only if crashes were considerate enough
to happen at convenient moments. A process can die after Blender has changed and
before anything is written, so there are two record types and the first one is
durable *before* any side effect:

- `OP_INTENT` — what is about to be done, and the state it is being done to
- `OP_RESULT` — what happened, linked to that intent

An intent with no terminal record is therefore evidence of an interrupted
operation. That is a fact worth having: it is the difference between "this may
have half-happened" and re-running a bevel because nothing remembered the first
one.

Deliberately thin. Not a replay engine, not a retention policy, not a query
language — the smallest durable spine that idempotency and, later, the artist
loop's experiment trace can both be built on, so they cannot disagree about what
happened.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Iterator

INTENT = "OP_INTENT"
RESULT = "OP_RESULT"
LEDGER_SCHEMA = 1


def ledger_root() -> Path:
    """Where ledgers live. Overridable so a test does not write to a real one."""
    override = os.environ.get("ROBOVISION_LEDGER_DIR")
    if override:
        return Path(override)
    return Path(os.environ.get("TEMP") or os.environ.get("TMPDIR") or "/tmp") / "robovision-ledger"


@dataclass(slots=True)
class OperationLedger:
    """One append-only file per world incarnation.

    Keyed by world rather than by process because that is the scope a logical
    operation belongs to: a bridge can be rebuilt while the world it was editing
    is verifiably still open, and the operations recorded against that world are
    still the operations of that world.
    """

    world: str
    path: Path
    sequence: int = 0

    @classmethod
    def open(cls, world: str) -> "OperationLedger":
        root = ledger_root()
        root.mkdir(parents=True, exist_ok=True)
        # The world incarnation is `rvworld:<uuid>`; the uuid alone is a legal
        # filename on every platform this runs on.
        _, _, tail = world.partition(":")
        return cls(world=world, path=root / f"{tail or world}.jsonl")

    def _append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Write one record and make it survive the process.

        Flushed and fsynced before returning, because a record that is still in a
        buffer when the editor dies proves nothing, and this file's entire value
        is what it can prove about a crash.
        """
        self.sequence += 1
        record = {"schema": LEDGER_SCHEMA, "sequence": self.sequence,
                  "world": self.world, **record}
        line = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def intent(self, **fields: Any) -> dict[str, Any]:
        """Record what is about to happen, before it happens."""
        return self._append({"type": INTENT, **fields})

    def result(self, intent_sequence: int, **fields: Any) -> dict[str, Any]:
        """Record what happened, linked to the intent that predicted it."""
        return self._append({"type": RESULT, "intent": intent_sequence, **fields})

    def read(self) -> Iterator[dict[str, Any]]:
        """Every record, in the order it was written."""
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    # A torn final line is what a crash mid-write looks like.
                    # It is not a reason to discard everything before it.
                    continue
                if isinstance(record, dict):
                    yield record

    def resume(self) -> dict[str, dict[str, Any]]:
        """Rebuild what this world already knows, after the bridge was replaced.

        Returns the terminal record for each idempotency key, and the bare intent
        for any operation that never reached one — which is exactly the evidence
        an interrupted operation leaves behind.
        """
        intents: dict[int, dict[str, Any]] = {}
        by_key: dict[str, dict[str, Any]] = {}
        highest = 0
        for record in self.read():
            highest = max(highest, int(record.get("sequence", 0)))
            if record.get("type") == INTENT:
                intents[int(record["sequence"])] = record
                key = record.get("idempotency_key")
                if key:
                    by_key[key] = {"intent": record, "result": None}
            elif record.get("type") == RESULT:
                origin = intents.get(int(record.get("intent", -1)))
                key = (origin or {}).get("idempotency_key")
                if key and key in by_key:
                    by_key[key]["result"] = record
        self.sequence = highest
        return by_key
