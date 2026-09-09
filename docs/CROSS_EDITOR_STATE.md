# Cross-editor state contract (proposed)

Status: **proposed, not implemented.** Nothing here is claimed as working. Each
section states the error cases it owes, because a contract that only describes
the happy path is not a contract.

The purpose is narrow: let an agent work for hours, across more than one editor,
without silently drifting out of sync, editing the wrong element, duplicating a
mutation after a retry, or believing evidence that was never proven.

## 0. Execution model: one authoritative writer

Every editor mutation passes through a single editor-thread executor. This is
already true of the Blender host and must stay true.

```text
socket worker            (parses bytes, never touches bpy / UnityEditor)
   -> validated command queue
   -> editor-thread executor   (the only writer)
   -> result queue
   -> transport response
```

Blender's Python API is not thread-safe, so the host polls a non-blocking socket
from `bpy.app.timers` and every `bpy`/BMesh call runs on the main thread. Unity
may accept connections asynchronously, but `SerializedObject`, `Undo`, prefab and
scene mutations run on the editor thread.

**Owed errors.** A request that arrives while the executor is not running gets
`EXECUTOR_UNAVAILABLE`, not a timeout. A queue that stops draining is a health
fault (§6), not silence.

## 1. Identity is layered, not one id

Collapsing lifecycle into a single `host_id` loses exactly the distinctions that
make stale state detectable. Seven levels, each with its own invalidation rule:

| level | identifier | changes when | survives |
| --- | --- | --- | --- |
| workspace / document | `rvws:<uuid>` | a different project or `.blend` is opened | process restart, reinstall |
| host process | `rvproc:<uuid>` | the editor process restarts | domain reload |
| runtime incarnation | `rvrt:<uuid>` | domain reload, assembly reload, add-on re-enable, bridge restart | nothing below it |
| scene revision | `int`, monotonic | authoritative scene state changes | within an incarnation |
| object identity | `b3d:<uuid>` / `unity:GlobalObjectId_*` | never, for saved objects | restart (asserted) |
| mesh revision | `int`, monotonic | editable mesh topology changes | within a document |
| observation | `rvobs:<uuid>` | every capture | referenced, never re-derived |

`rvws` is persisted **in the document** (a Blender scene custom property, a Unity
`ProjectSettings` asset), not derived from a filesystem path, so renaming or
moving a project does not silently create a new workspace. If the stored id is
missing it is minted and written on first contact; if a document is copied, the
duplicate is detected the same way duplicate object ids already are.

The system can then answer: *this decision came from observation O of mesh
revision M, scene revision S, runtime R, process P, workspace W.* Stale state has
nowhere to hide.

**Owed errors.** `STALE_RUNTIME` when a request carries an `rvrt` that is no
longer current. `WRONG_WORKSPACE` when it names an `rvws` this host does not
serve. Both are distinct from `NOT_FOUND`.

## 2. Addressing a host

A loopback port does not identify an editor; two Blenders and two Unitys can be
open. A request may carry `workspace` and/or `runtime`. If it names neither and
more than one host could serve it, the host answers `AMBIGUOUS_HOST` with the
candidates rather than guessing. No registry process is required — each host
advertises itself, and a client holding identity connects directly.

## 3. Topology handles are revision-scoped

Object ids are durable. Face, edge and vertex references are not, and BMesh
operations invalidate indices freely.

```text
object:      b3d:4a82...
sub-element: { mesh_revision: 31, face: 127 }
```

At mesh revision 32 that handle is `STALE_HANDLE`. Face 127 at revision 31 is not
face 127 at revision 32, and the host must never pretend otherwise. This is
already enforced; the contract adds what an operation owes on the way out:

- `created` — new elements, addressed at the new revision
- `invalidated` — handles the caller held that are now void
- `remap` — old to new provenance where the operation can express it

An operation that changes topology without reporting these leaves the caller
guessing, which is the failure mode the revision scoping exists to prevent.

## 4. Transactions are RoboVision's, not native undo's

Native undo is a human convenience and a useful mechanism, but it must not be the
whole semantic definition. A transaction tracks what it owns: objects created and
deleted, hierarchy changes, mesh datablocks touched, transforms, modifiers,
materials and nodes, components and properties, prefab changes, generated assets,
the revisions it started from, and the pre-state needed to restore.

The guarantee is: begin, mutate many entities, validate, detect a defect, roll
back, and **prove** the authoritative state was restored — proof by fingerprint
comparison, not by trusting that undo did the right thing.

### 4.1 Ownership and adoption

A transaction belongs to the connection that opened it. Mutations from other
connections are refused with `TRANSACTION_FOREIGN`; reads stay open.

The current behaviour — any client may finish an orphan once the owner's socket
drops — is too weak. A dropped TCP connection is not authorization. Replace it
with explicit adoption:

```text
transaction.adopt(transaction, recovery_token) -> ownership | ADOPTION_REFUSED
```

- the owner reconnecting with its recovery token adopts its own transaction
- a supervisor client holding the recovery capability may adopt
- any other client may **inspect** an orphan and see it reported in health and
  in `system.hello`, but gains no authority over it
- forced recovery remains available and is audited as an elevated operation

**Owed tests.** Owner disconnect; owner reconnect and adopt; adoption by a
supervisor; refusal of an unauthorized adopter; domain reload with a transaction
open; process restart with a transaction open; a stale transaction id after
either.

## 5. Optimistic concurrency covers humans too

The agent must not assume it is the only writer. If it observed revision 450 and
a person moved something before it acted, the mutation is refused and the agent
re-observes. Editor notifications are inputs to this, never the authority:
Blender's depsgraph handlers, `bpy.msgbus`, undo/redo and load/save handlers;
Unity's `ObjectChangeEventStream`, hierarchy and project changes, `Undo`
post-processing and `AssetPostprocessor`. Every mutation still re-reads
authoritatively before it is allowed to run.

## 6. The journal, and sticky uncertainty

```text
scene.changes_since(sequence) -> { sequence, revision, epoch, events[], certain }
```

Events carry a monotonic `sequence`, the `revision` they produced, a `type`,
affected **stable ids** (never names), a `path` where known, a `source`
(`agent` with its request id, `editor`, or `unknown`), and a timestamp.

Both editors under-report. `bpy.msgbus` does not fire for a viewport drag;
`ObjectChangeEventStream` is a per-frame view a batch operation can outrun. So
certainty is tracked as an **epoch**:

- the journal carries `epoch`, incremented only by an authoritative snapshot
- when the host cannot attribute a change, it emits `RESYNC_REQUIRED` and clears
  certainty for the current epoch
- **once cleared, certainty is sticky.** A later clean-looking notification does
  not restore trust. Only an authoritative snapshot opens a new epoch
- `changes_since` with a sequence from a superseded epoch returns
  `EPOCH_SUPERSEDED`; with a discarded sequence, `SEQUENCE_TOO_OLD`

The journal is a performance optimization for polling. It never substitutes for a
fingerprint in a proof, and transactions keep comparing deep fingerprints.

## 7. Idempotency

A lost reply must not become a second bevel.

Every mutating request may carry `idempotency_key`. The host keeps a record per
`(workspace, runtime, key)`:

| situation | behaviour |
| --- | --- |
| duplicate while the first is still executing | `IN_PROGRESS`, retryable, no second execution |
| duplicate after completion | the original response, replayed verbatim, flagged `replayed: true` |
| duplicate after a runtime incarnation change | `INDETERMINATE` — the host cannot prove what happened; the agent must re-observe |
| duplicate inside a transaction | scoped to that transaction and discarded with it |
| timeout with unknown status | the client retries with the same key and gets one of the above, never a silent second apply |

The record is runtime-scoped and in-memory by default. Persisting it across a
restart is possible but would need the outcome to be durable too, so the honest
answer across an incarnation boundary is `INDETERMINATE` rather than a guess.

## 8. Request and response envelopes

Minimal coherent version first; fields are added when a test needs them, not
because they appear in a list.

A mutating request carries: request id, `idempotency_key`, protocol version,
target ids, `if_revision`, `expected_mesh_revision` where the operation is
topology-indexed, transaction id, and canonical typed parameters.

A response reports an explicit outcome — `applied`, `noop`, `rejected`,
`retryable`, or `indeterminate` — plus revision before and after, ids touched,
created, deleted, handles invalidated, and any recovery outcome. `noop` and
`applied` must be distinguishable; an agent that cannot tell them apart cannot
tell whether its correction landed.

## 9. Health is not a ping

A responding socket proves the socket responds. Health reports each of these
separately, and anything unknown is reported as unknown rather than healthy:

transport listening · host attached · workspace and document identity · executor
alive · queue draining · scene readable · mutation path healthy · perception path
healthy · transaction subsystem healthy (including any orphan) · runtime
incarnation current · journal epoch and certainty.

## 10. Perception is two systems

**Semantic** perception is proprioception: hierarchy, source and evaluated
transforms, bounds and dimensions, mesh statistics, topology, manifold state,
ngon/triangle/quad counts, tiny faces, open boundaries, normals, symmetry, UVs,
the source-versus-evaluated distinction, raycasts, nearest-surface queries, BVH
overlap, clearance, modifier and constraint effects, material and node metadata.

**Visual** perception is pixels: colour, solid, wireframe, depth, normals, world
position, object-id and material-id masks, silhouette, face orientation, UV
views, selection and topology overlays, rendered comparisons.

Both share view and projection provenance so a pixel can be correlated with the
geometry that produced it. An observation id (`rvobs`) names the exact capture an
agent reasoned from, so a later claim can be traced to the evidence behind it.

Unity additionally needs a **deterministic explicit-camera capture path** that
does not depend on whichever SceneView happens to be focused, for headless and
CI use. SceneView fidelity stays a separate, honestly blocked GUI proof; the
explicit-camera path does not replace it or discharge it.

## 11. Audit and replay need environmental provenance

Canonical parameters and stable ids do **not** by themselves make a session
replayable. Replay can depend on editor version, RoboVision schema version,
capability hash, fixture starting fingerprint, source asset hashes, import
settings, package versions, floating-point behaviour, procedural seeds and
generated id mapping.

So each record carries the operation, canonical params, request and idempotency
identity, revisions either side, workspace/process/runtime identity, transaction,
evidence ids, timing, result, error and recovery outcome, and the environment
identity above. The claim is then bounded and true:

> replayable against a declared fixture and environment

not "deterministic" in the abstract. A reproducible bug becomes a replay
regression fixture, which is what the Blender and Unity gates already do by hand.

## 12. Lifecycle invalidation

What survives each boundary. Unity's column is evidence; Blender's is largely
unproven and is marked so rather than assumed to mirror Unity.

| boundary | workspace | process | runtime | scene rev | object id | mesh handle | transaction | journal seq | idempotency | observation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| in-session edit | keep | keep | keep | bump | keep | revision-scoped | keep | continue | keep | keep |
| undo / redo | keep | keep | keep | bump | keep | invalidate | contaminate | continue, `uncertain` | keep | keep |
| file save | keep | keep | keep | keep | keep (may upgrade) | keep | keep | continue | keep | keep |
| file reopen / load other | keep or change | keep | **new** | reset | durable only | invalidate | abort | reset | drop | drop |
| domain / add-on reload | keep | keep | **new** | reset | durable only | invalidate | orphan then adopt | reset | drop | drop |
| editor restart | keep | **new** | **new** | reset | durable only | invalidate | drop | reset | drop | drop |

Unity has evidence for the last two rows. Blender has evidence for none of them,
which is the next thing to fix.

## 13. Security posture

Loopback binding is necessary and not sufficient. The direction: a session
capability handshake, workspace verification so a client cannot address the wrong
document, capabilities scoped so destructive operations are separable from
read-only ones, filesystem access confined to the project root, external network
access as a distinct privilege, and arbitrary Python/C# disabled by default with
every elevated use audited. Arbitrary code stays an emergency escape hatch, not
part of the normal control path.

## 14. MCP is an adapter

```text
Astra / other clients -> MCP or other adapters -> RoboVision core -> Blender / Unity hosts
```

The core owns schemas, operation semantics, identity, revisions, transactions,
idempotency, capability negotiation, the journal, audit and replay, lineage and
validation. The hosts do not know which external protocol is in use, and MCP
stays replaceable.
