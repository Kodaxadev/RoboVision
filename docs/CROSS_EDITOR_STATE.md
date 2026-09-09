# Cross-editor state contract (proposed)

Status: **proposed, not implemented.** This is the design the reliability
milestone is meant to build against. Nothing here is claimed as working.

The three pieces below exist to let an agent work for hours across more than one
editor without either rescanning everything or quietly drifting out of sync.

## 1. Host and session identity

A loopback port does not identify an editor. Two Blenders and two Unitys can be
open at once, and an agent that assumes `127.0.0.1:9877` is the right one will
eventually edit the wrong document.

`system.hello` gains an identity block:

```json
{
  "host_id": "rvhost:9f2c1e7a4b8d",
  "host_session_id": "rvsess:2026-09-08T23:14:02Z:1f0c",
  "editor": "blender",
  "editor_version": "5.2.1",
  "document": {
    "path": "D:/Frontier/vehicles/hauler.blend",
    "name": "hauler.blend",
    "saved": true
  },
  "process": { "pid": 24188, "started_utc": "..." },
  "capability_hash": "sha256:...",
  "scene_revision": 691
}
```

- `host_id` is stable for one editor process bound to one document. It is
  derived from editor kind, process identity and document path, so reconnecting
  to the same editor yields the same id.
- `host_session_id` changes on every process start. A client holding one from a
  previous session knows its session-scoped handles are void without having to
  test them.
- `capability_hash` lets a client cache the tool catalogue and re-fetch only
  when it changes.

**Addressing rule.** A request may carry `host_id`. If it is absent and exactly
one host matches, that host serves it. If more than one could match, the host
returns `AMBIGUOUS_HOST` listing the candidates rather than guessing. This is
the same principle already applied to identity and topology: refuse rather than
address the wrong thing.

A registry process is deliberately *not* required. Each host advertises itself;
a client that already holds a `host_id` connects directly.

## 2. Change journal

Deep fingerprints are correct and, on a large scene, too expensive to take after
every action. The journal exists to make the common case cheap without making
the guarantee weaker.

```text
scene.changes_since(sequence) -> { sequence, revision, events[], uncertain }
```

Each event carries:

| field | meaning |
| --- | --- |
| `sequence` | monotonic per host session, never reused |
| `revision` | the scene revision the event produced |
| `type` | `OBJECT_CREATED`, `OBJECT_DELETED`, `OBJECT_TRANSFORMED`, `PROPERTY_CHANGED`, `TOPOLOGY_CHANGED`, `ASSET_REIMPORTED`, `SCENE_OPENED`, `SCENE_SAVED`, ... |
| `ids` | stable ids affected — never names |
| `path` | property or data path when known |
| `source` | `agent` (with the request id), `editor`, or `unknown` |
| `timestamp` | host clock |

**Notifications are hints, not truth.** Both editors under-report:
`bpy.msgbus` does not fire for a viewport drag, and Unity's
`ObjectChangeEventStream` is a per-frame view that a batch operation can outrun.
So the journal carries an explicit `uncertain` marker:

- when the host cannot attribute a change to specific ids, it emits
  `RESYNC_REQUIRED` and sets `uncertain: true`
- a client seeing `uncertain` must fall back to `scene.snapshot` for the region
  it cares about
- `changes_since` with a sequence the host has already discarded returns
  `SEQUENCE_TOO_OLD` rather than a partial answer

The journal never replaces the fingerprint for proofs. Transactions keep
comparing deep fingerprints; the journal only makes routine polling cheap.

Sources to combine per host:

- **Blender** — `depsgraph_update_post`, `bpy.msgbus` for RNA properties,
  undo/redo handlers, `load_post`/`save_post`, plus the authoritative resync
  already used before every mutation.
- **Unity** — `ObjectChangeEventStream`, `hierarchyChanged`, `projectChanged`,
  `Undo.postprocessModifications`, `AssetPostprocessor`, plus the same
  authoritative resync.

## 3. Audit journal

Every mutating operation appends one record. This is not model reasoning; it is
what RoboVision actually did and what it observed.

```json
{
  "op_id": "rvop:00000412",
  "host_id": "rvhost:9f2c1e7a4b8d",
  "host_session_id": "rvsess:...",
  "request_id": "agent-88",
  "client": 3,
  "revision_before": 812,
  "method": "mesh.bevel",
  "params_canonical": { "object": "b3d:...", "expected_mesh_revision": 31, "width": 0.003 },
  "result": { "mesh_revision": 32 },
  "revision_after": 813,
  "transaction": "tx:...",
  "evidence": ["rvpf:8912"],
  "timing_ms": 41.7,
  "outcome": "ok"
}
```

Failures record the error code and the recovery outcome, so
`MUTATION_RECOVERY_INCOMPLETE` is visible in the history rather than only in the
response the client happened to read.

Because params are canonical and every reference is a stable id, a recorded
session is replayable against the same fixture:

```text
replay.run(session, fixture) -> per-op comparison of revisions and fingerprints
```

That turns a reproducible bug into a regression fixture, which is the same move
the Blender and Unity gates already make by hand.

## Lifecycle events the contract must survive

Proven for Unity today: domain reload, editor restart, package re-resolution.
Proven for Blender today: nothing at this layer.

| event | Blender | Unity |
| --- | --- | --- |
| in-session edit | resync before every mutation | same |
| undo/redo | handler + resync | `Undo.undoRedoPerformed` + resync |
| file save/reopen | **not yet proven** | scene reopen proven |
| domain/script reload | n/a | proven |
| editor restart | **not yet proven** | proven |
| package/add-on reload | **not yet proven** | proven |

The gaps in that table are the reliability milestone's first work, and they are
listed as gaps rather than assumed to behave like their Unity counterparts.
