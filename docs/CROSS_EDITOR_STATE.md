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

Nine levels, moved to [IDENTITY.md](IDENTITY.md) with the rules for what rotates
when, in which host, and on what evidence — including §1.1 document identity and
forks, §1.2 control-plane metadata, and §1.3 what counts as a different editing
context.

The short version: **workspace**, **document identity**, **host process**,
**bridge incarnation**, **world / edit-context incarnation**, **journal
incarnation**, **scene revision**, **object identity**, **mesh revision**,
**observation**. Each answers a question the others cannot, so the system can
say: *this decision came from observation O, in world incarnation D of document
W, at scene revision S and mesh revision M, through bridge B in process P.*

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

An agent polls `scene.changes_since(cursor)` rather than re-reading the scene.
Cursors name the world incarnation and certainty epoch they were issued in,
losing certainty is sticky, one reconciliation path attributes every change, and
scene revision tracks state rather than commands run. The full contract is in
[JOURNAL.md](JOURNAL.md).

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
| duplicate after a bridge incarnation change | `INDETERMINATE` — the host cannot prove what happened; the agent must re-observe |
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

### 11.1 Control-plane changes must be auditable

Control-plane metadata is excluded from *authored* state (§1.2), which is
correct, and it leaves a gap the audit journal has to close rather than inherit.

Both measured cases are closed, and what closed them is the rule the audit
journal inherits rather than reinvents: an identity transition is reported as
one when the host can prove it, and not claimed at all when it cannot.

**Blender.** Deleting an object's `_robovision_id` used to mint a different id,
which reached the client as `OBJECT_DELETED` plus `OBJECT_CREATED` for an object
that never went anywhere. The session owner registry knows which id this live
object already owns, so the property is written back and the event is
`IDENTITY_REPAIRED`, source `host`, carrying the basis for the claim. The public
id does not change, so no authored state moved.

**Unity.** An unsaved object has only a session handle, and saving is what earns
it a durable `GlobalObjectId`. The public address really does change, and the
retired handle still resolves to the same live object, so the pair is lifted out
of the object diff and reported as `IDENTITY_UPGRADED` with `previous_id`, `id`
and the basis.

Two events, not three, because a third would be a claim without evidence. Where
continuity cannot be proven — a property removed from the file itself, an id
minted after a reopen, a session handle that died with its domain — nothing is
linked and the delete and create stand, which is what the host actually knows.

Neither advances the scene revision: that counter names authored state, and
re-addressing an object is the host's bookkeeping. **Still owed by the audit
work:** a control-plane revision or equivalent sequence of its own, so a client
can tell "nothing has happened" from "nothing authored has happened", and an
audit record that exposes control-plane metadata changes generally rather than
only these two.

## 12. Lifecycle invalidation

`keep` survives, `new` rotates, `reset` restarts from its initial value, `drop`
is discarded, `invalidate` means existing handles must now fail rather than
resolve.

| boundary | workspace | document id | process | bridge | world incarnation | scene rev | object id | mesh rev | mesh handle | transaction | journal seq | idempotency | observation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| in-session edit | keep | keep | keep | keep | keep | bump | keep | bump on topology | revision-scoped | keep | continue | keep | keep |
| undo / redo | keep | keep | keep | keep | keep | bump | keep | bump | invalidate | contaminate | continue, uncertain | keep | keep |
| save (same path) | keep | keep | keep | keep | keep | keep | keep | keep | keep | keep | continue | keep | keep |
| Save As | keep | **new** (§1.1) | keep | keep | keep | keep | keep | keep | keep | keep | continue | keep | keep |
| reopen same document | keep | keep | keep | keep | **new** | reset | keep | keep | invalidate | abandon | reset | drop | drop |
| load a different document | keep | keep (that file's own) | keep | keep | **new** | reset | durable only | that file's own | invalidate | abandon | reset | drop | drop |
| add-on disable/enable or reload | keep | keep | keep | **new** | **new** | reset | keep | keep | invalidate | drop | reset | drop | drop |
| Unity domain / assembly reload | keep | keep | keep | **new** | keep if verified (§1.3) | resume | keep | keep | invalidate | orphan, then adopt (§4.1) | reset | drop | drop |
| editor process restart | keep | keep | **new** | **new** | **new** | reset | durable only | keep | invalidate | drop | reset | drop | drop |

Two rows deserve their reasoning.

**Reopening the same document** rotates the world incarnation but not the
bridge: the same code is running, its sockets are still bound, and its module
state survived. Everything scoped to the loaded world is void; everything scoped
to the code is not.

**A bridge reload does not have to rotate the world**, and the two hosts differ
here for a reason that is about evidence rather than taste. The open file is
untouched either way; the question is whether the rebuilt bridge can *prove* it
is looking at the world the previous one left.

Unity can. `SessionState` survives an assembly reload and dies with the process,
so the previous world identity crosses the reload, and the editing context is
read back out of the editor and has to match it exactly before it is adopted —
measured, not assumed: the loaded scene handles are identical either side of a
play mode round trip. The bridge is new, the journal is new and its cursors are
refused as `JOURNAL_REPLACED`, but durable references keep resolving and the
scene revision resumes. Blender cannot: an add-on reload takes the module's own
memory with it and leaves nothing session-scoped to verify against — a custom
property would be authored state, and the file may not even be saved — so it
rotates the world and says why. Claiming continuity a host cannot establish
would be exactly the kind of false evidence the rest of this document exists to
prevent.

Unity's domain reload keeps the transaction — orphaned, then adopted per §4.1 —
because the scene it describes is still there, which is the one thing that does
survive.

All three incarnations are implemented in both hosts now. In Unity the bridge is
minted by the host singleton's constructor, so a domain or assembly reload
rotates it by construction; the world follows the editing context as defined in
§1.3, and the journal rotates whenever its history restarts.

Evidence today. Blender: save, reopen, load-other and process restart are
asserted by `tests/blender/lifecycle_document.py` and
`tools/blender-restart-gate.sh`. Unity: domain reload, editor restart and package
re-resolution are asserted by the EditMode suite and
`tools/unity-restart-gate.sh`. Blender add-on
disable, reload and re-enable are asserted by `tests/blender/lifecycle_addon.py`,
which is a stronger claim than detach/reattach: the module is purged and rebuilt,
so every callback is a new object. Unproven and marked so rather than assumed:
undo/redo interleaved with a save, and every row's idempotency and observation
column, neither of which exists yet.

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
