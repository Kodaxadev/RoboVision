# Acceptance gates

Tool count is not a release criterion. RoboVision advances when end-to-end gates pass repeatedly.

Each gate below records what its evidence actually covers. "Passing" means the
listed run reproduces, not that the subsystem is finished.

## Gate 0 — protocol substrate

- protocol parser rejects malformed/stale-version requests
- deterministic canonical fingerprints
- capability discovery
- editor-thread rule documented and enforced by architecture
- host-independent CI green

`tests/blender/gate0_transport.py` drives a live host over TCP with the shipped
`RoboVisionClient` rather than calling `dispatch` in-process: capability
discovery, typed errors surviving the wire, optimistic concurrency, 40
sequential calls on one connection, two pipelined requests in a single packet,
malformed JSON, and an oversized request. The worker thread performs socket I/O
only while Blender's main thread pumps `poll()`, so the editor-thread rule holds
under test.

## Gate 1 — Blender closed loop

Starting from a known `.blend` fixture, an external client must perform the following **without arbitrary Python execution**:

1. `system.hello`
2. `scene.snapshot`
3. `viewport.capture`
4. `transaction.begin`
5. create an object
6. mutate mesh topology
7. add and apply a modifier
8. `mesh.validate`
9. `viewport.capture`
10. `scene.diff`
11. `transaction.rollback`
12. prove the begin fingerprint is restored

Run the cycle repeatedly. Fail on crash, leaked objects, duplicate IDs, stale element acceptance, rollback mismatch, or context-dependent nondeterminism.

### Negative paths — `tests/blender/gate1_adversarial.py`

The happy path does not decide whether an agent can trust the host. These do:

1. a mutation that fails halfway leaves no partial state and reports its recovery
2. a mutation carrying a drifted `if_revision` is refused and changes nothing
3. a topology-indexed mutation carrying a stale revision is refused, and the
   revision advances after an out-of-band edit
4. no topology-indexed mesh mutation runs without `expected_mesh_revision`
5. identity survives datablock copy, `object.duplicate` and the operator path,
   and the original keeps the id the agent is holding
6. Edit Mode mutation is refused rather than silently discarded on mode exit
7. rollback restores modifier settings, not merely modifier presence
8. an out-of-band edit during a transaction blocks automatic rollback until forced
9. rollback leaves objects the transaction never touched byte-identical

### Repeatability — `tests/blender/gate1_soak.py`

One green run proves almost nothing. The soak repeats the full cycle against a
single long-lived Blender session and runtime, asserting per cycle that the
fingerprint returns to baseline, no datablocks leak, identities stay unique with
no repairs required, the revision advances, and a bystander object created
before the run is unchanged after it.

### Evidence on record

| run | editor | result |
| --- | --- | --- |
| gate0/1/1-adversarial/1-soak/2 | Blender 5.2.1 LTS, Linux CI | pass |
| gate0/1/1-adversarial/1-soak/2 | Blender 5.1.2, Windows | pass |
| gate0, semantic query, document lifecycle, add-on lifecycle, journal | Blender 5.1.2, Windows, headless | pass, current checkpoint |
| soak 500 cycles | Blender 5.1.2, Windows | pass, mean 47.8ms, max 55.0ms |
| soak 250 cycles | Blender 5.2.1 LTS, Linux CI | pass, mean 35.1ms, max 40.9ms |

The Gate 1 baseline fingerprint is identical on both platforms and versions.

The two rows are not interchangeable and are kept apart deliberately. CI runs
**Blender 5.2.1 LTS** on Linux and covers the whole sequence, including the four
gates that need an interactive editor — gate 1, its adversarial paths, the soak
and gate 2 — under xvfb. The local Windows editor is **5.1.2**, and the five
headless gates are what run there; the interactive four report
`UNDO_UNAVAILABLE` and `INVALID_CONTEXT` in `--background` by design, so a local
headless run neither covers nor contradicts them.

### Document and process lifecycle

`tests/blender/lifecycle_document.py` (headless) asserts what each in-process
boundary owes: saving keeps object ids, mesh revisions and the fingerprint;
reopening the same document resolves the same objects with the same fingerprint;
loading a different document mints a new world incarnation, resets the scene
revision, stops the previous document's ids resolving, leaves no stale identity
owner entries, and abandons an open transaction with `DOCUMENT_CHANGED` instead
of leaving it pointing at a file that is gone.

`tools/blender-restart-gate.sh` runs two headless Blenders in sequence against
one document, verifying between them that the first process is gone and its port
released. The second process resolves the same object ids, reproduces the same
fingerprint, reports a different bridge and world incarnation, resets the
revision, holds
no transaction, rebinds the port, and is driven by the shipped Python
`RoboVisionClient`.

The gauntlet also covers unsaved-to-saved, Save As, three consecutive reopens of
one document, an A to B to A round trip, a snapshot handle from a world that is
no longer open failing with `STALE_WORLD`, and reattaching the bridge, which
mints a new bridge and a new world incarnation. Detaching and reattaching a
live runtime is only that; the add-on lifecycle it resembles is proven
separately below.

Not proven for Blender: undo and redo interleaved with a save. Blender's undo
does not restore state in background mode, and this gauntlet is headless, so the
claim is not made here. The host now refuses rather than pretending: a rollback
that would actually need to undo something returns `UNDO_UNAVAILABLE` in
background, and `transaction.begin` reports `verified_rollback: false` there.

### Change journal

`tests/blender/journal_gate.py` (headless) runs fourteen scenarios in two
modules: `journal_events.py` for what the host reports and who it blames,
`journal_cursors.py` for what a client's position is allowed to mean.

Events: a monotonic sequence with no gaps or reuse, agent mutations attributed to
the request that caused them, editor-side changes attributed separately, topology
changes distinguished from ordinary ones, and re-reading a range returning the
same answer.

The refusals matter as much as the events, because an empty event list reads as
"nothing changed":

- an edit whose notification never arrived, discovered at the next mutation's
  resync, is journalled with editor attribution — it used to be absorbed into the
  new baseline while the journal claimed certainty, and the client saw the
  revision advance twice for one event
- an authoritative `scene.snapshot` rebuilds the baseline even when the host
  believed itself certain and its dirty flag was clear
- a change the host can see but cannot attribute drops certainty, and a later
  perfectly ordinary mutation does **not** restore it; only an authoritative
  snapshot opens a new epoch
- a cursor from a superseded epoch is refused with `EPOCH_SUPERSEDED`, one from a
  replaced world with `STALE_WORLD` — asserted with both sides at epoch 1,
  since epoch numbers collide across worlds and the case is otherwise proven
  by accident — one from a journal that was rebuilt inside a world that survived
  with `JOURNAL_REPLACED`, one past the retention window with `SEQUENCE_TOO_OLD`,
  and one ahead of the journal, malformed, or using the old bare `after`
  parameter with `INVALID_PARAMS`
- a successful mutation that changes nothing reports `outcome: noop`, leaves the
  scene revision alone and writes no events; three shapes of it are covered —
  a transform to the current location, a modifier setting written to the value it
  already holds, and clearing a parent that was never set

The journal is a polling optimisation. Nothing that has to be proved uses it:
transactions still compare deep fingerprints.

### Idempotency, seeds and the operation ledger

`tests/blender/idempotency.py` (headless) covers fourteen scenarios, written
after measuring what a duplicate delivery actually did: a resent create made two
objects, a resent delete reported `NOT_FOUND` for work that had succeeded, and a
resent absolute transform reported `noop` — protection by accident of the
operation, not by design.

A lost reply now replays the original response for a create, a delete and an
absolute transform, and the transform is asserted `replayed` rather than `noop`
so accidental idempotence cannot pass for the real thing. The same key with
different parameters or a different seed is `IDEMPOTENCY_MISMATCH` with both
recipe hashes; the same recipe with a fresh key executes deliberately, which is
what a candidate branch needs. A retry the host has no record of, an operation
interrupted between its intent and its result, and a retry naming a replaced
world are all refused without executing. A transaction that ended keeps its
tombstones, so a retry after a rollback is told what happened instead of
resurrecting the operation.

Seeds are enforced rather than encouraged: a test-only stochastic tool is
refused `SEED_REQUIRED` before any side effect, its seeded retry replays the same
recorded randomness, and the registry is asserted to reject a seeded tool with no
channels, seeds on a tool claiming exactness, and an undeclared determinism
class. The ledger is asserted to write `OP_INTENT` before the side effect and
`OP_RESULT` after it, with the coordinate frame recorded on every intent.

Not yet covered: two real clients racing one key over TCP, and a bridge reload
that resumes a verified world — the Blender runtime never resumes one, so its
ledger resume path is exercised directly and the real reload belongs to Unity.

### Transaction ownership

`tests/blender/transaction_ownership.py` (headless) is the first Blender gate
that needs two real connections, because ownership is exactly what an in-process
harness cannot see: every in-process call looks like the same local client, and a
disconnect means nothing without a socket to drop.

Two clients, one editor. A second connection reads freely and is refused
`TRANSACTION_FOREIGN` for a mutation, a commit and a rollback. The owner
disconnects; the transaction is reported orphaned, and the stranger is still
refused — `TRANSACTION_ORPHANED` now — for continuing it, finishing it, or
adopting it with a wrong token, and that refusal changes nothing about the
transaction. The owner reconnects, presents the token issued at begin, takes
ownership, and the token it used is dead afterwards because adoption rotates it.
`system.hello` is asserted not to contain the credential anywhere.

### Read consistency

`tests/blender/journal_reads.py` covers the policy that decides what a
response's revision is worth. Measured before it existed: with a notification
missed, `scene.describe` returned an object at its genuinely current position
stamped with the scene revision and journal cursor of the state before it, and
journalled nothing — and five other state-bearing reads did the same.

Every tool now declares `authoritative`, `notified` or `independent`, the
dispatcher enforces it, and the response reports which it got. The gate asserts
that a state-bearing read after a missed notification returns current state with
an advanced revision and an editor-attributed event whose revision matches the
response; that journal polling and the capability catalog deliberately do *not*
reconcile, so the cheap class stays cheap; and that the set of non-authoritative
tools is exactly the reviewed list, so a new tool cannot drift into it silently.

It also pins the control-plane boundary: RoboVision's `_robovision_*` storage
stays out of the authored custom properties a snapshot reports, while identity
and mesh revision still reach the fingerprint through their canonical fields.
Rewriting a bookkeeping property to the value it already holds is a no-op; an
authored property is a change.

### Bridge and add-on lifecycle

`tests/blender/lifecycle_addon.py` (headless) separates three events that were
being conflated. Detach and reattach on a live runtime rotates the bridge and
world incarnation, invalidates cursors from before it, and leaves exactly one
set of callbacks registered across repeated cycles.

A genuine add-on lifecycle is a stronger claim and is now made: enable, install
handlers, `addon_utils.disable` — which runs the shipped `unregister` — then a
module purge and re-enable, with the reloaded module verified to be a different
object. Before this gate existed, that cycle left `load_pre` at 3, `load_post` at
4 and `save_post` at 2: `remove_handlers()` removed only the depsgraph handler,
and the `@persistent` load and save callbacks accumulated one dead entry per
cycle, each holding its whole module alive. Callbacks now carry the token of the
load that registered them, so a load whose `unregister` never ran is swept — and
the gate asserts the sweep leaves another add-on's handlers untouched.

### Unity reconciliation, change journal and world identity

Moved to [GATES_UNITY.md](GATES_UNITY.md): the one reconciliation path, the read
consistency policy, the ported journal, what counts as a different editing
context, and the identity transitions saving makes visible.

### Not yet proven at Gate 1

- undo-driven recovery outside Object Mode; the host reports `RECOVERY_UNSAFE`
  rather than attempting it
- linked-library and multi-scene identity beyond the derived-id path
- concurrent clients issuing interleaved mutations
- behaviour across file load/save and across Blender restarts

## Gate 2 — perception bundle

Color/solid/wireframe captures plus depth, normals, object IDs and material IDs are aligned to the same view/projection metadata. The agent can identify a deliberately introduced intersection and select the implicated object IDs.

## Gate 3 — production modeling

Typed coverage for hard-surface modeling, materials/nodes, UVs, decals, geometry nodes, curves, sculpt/retopo support, rigging/animation, import/export, asset catalog, rendering and batch validation. Multi-view visual verification is standard.

## Gate 4 — Unity parity

Moved to [GATES_UNITY.md](GATES_UNITY.md), with what a live Editor has proven,
the defects the gate found, editor restart and package re-resolution, the
concurrency policy, the soak, the identity contract, and what stays unproven.

## Gate 5 — Blender → Unity lineage

RoboVision can trace a source Blender object through export/import to Unity asset GUID, prefab and scene instance; a defect in Unity can be mapped back to its Blender source identity.

## Gate 6 — agent-grade autonomy

Long jobs, cancellation, progress, event subscriptions, scene-change streaming, artifact resources, deterministic macros, replay, audit log, policy limits, recovery after editor/domain reload, and stress/soak tests.
