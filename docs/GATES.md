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
| health, cross-editor public flow | Blender 5.1.2, Windows, headless | pass |
| asset truth: geometry, coordinate/scale, comparison | Blender 5.1.2, Windows, headless | pass |
| asset truth: view coverage, reference shape, pattern | Blender 5.1.2, Windows, headless | pass |
| asset truth: edit locality | Blender 5.1.2, Windows, headless | pass |
| asset truth: correction evaluator, accept + 4 reject/rollback branches | Blender 5.1.2, Windows, **interactive** | pass |
| Artist Loop strict autonomous delivery | Blender 5.1.2, Windows, headless | pass |

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
resurrecting the operation — which the Unity port found this gate was not
actually testing. The manager never told the invocation ledger anything, and the
gate wrote the outcome by hand before asserting it, so the assertion proved its
own edit; a real client's retry would have been answered with no outcome at all.
The manager now reports a terminal transaction to the ledger on both hosts and
the gate asserts what the host recorded.

Seeds are enforced rather than encouraged: a test-only stochastic tool is
refused `SEED_REQUIRED` before any side effect, its seeded retry replays the same
recorded randomness, and the registry is asserted to reject a seeded tool with no
channels, seeds on a tool claiming exactness, and an undeclared determinism
class. The ledger is asserted to write `OP_INTENT` before the side effect and
`OP_RESULT` after it, with the coordinate frame recorded on every intent.

Six more scenarios close what reviewing the implementation found. A failed
operation whose recovery proved nothing applied is recorded `proved_not_applied`
rather than deleted, and a bridge rebuilt from the ledger reaches the same
conclusion instead of replaying a response that was never produced. A generic
handler failure whose own recovery fails no longer leaves its key reserved for
the life of the bridge. One key means one side effect whatever transaction it
ran in. A replay reports `original_execution` separately, so a client cannot read
the current revision as the one the operation ran at. The autonomous contract
refuses an authored mutation missing its world, revision, key, attempt or seeds.
And transaction control is asserted side-effecting while not mutating, so
"does not advance the revision" cannot be read as "safe to repeat".

Not yet covered: two real clients racing one key over TCP, and a bridge reload
that resumes a verified world — the Blender runtime never resumes one, so its
ledger resume path is exercised directly and the real reload belongs to Unity.

### The Astra-facing path

`tests/blender/mcp_session.py` (headless) drives the real `mcp_server._call`,
because the host's guarantees are only worth what the public path can use.
Measured before it existed: `transaction.begin` succeeded and by the next MCP
tool call the transaction was already orphaned — the connection that opened it
closed as that call returned — so the mutation and the rollback were both refused
`TRANSACTION_ORPHANED`. Transactions were unusable for the Artist Loop.

One session per host now, serialized, reconnected deliberately. The gate drives
the **public session helpers**, not a test that generates secrets itself, because
what needs proving is the ergonomics an agent actually gets: it proves a
transaction survives begin, three mutations and a read as separate MCP calls;
that a dead socket orphans it while the credential outlives the socket it was
meant to outlive; that the reconnected session is treated as a stranger rather
than the old owner until it adopts; that the session's own credential adopts it
and rotates to a replacement it already held; that a session which never opened a
transaction holds nothing for it (`NO_RECOVERY_CREDENTIAL`); and that the strict
autonomous contract is reachable from `rv_call`, with a retry replaying rather
than creating a second object.

An autonomous `transaction.begin` is observation-bound. The gate observes a
revision, lets a human edit the scene, and asserts the planned begin is refused
`STALE_REVISION` **before any transaction is opened** — a checkpoint taken from a
scene that moved is a rollback target nobody chose. A begin missing its
`expected_world`, `expected_coordinate_contract` or `if_revision` is
`CONTRACT_VIOLATION`. Terminal records keep the proof that made them terminal, so
a late commit of a finished transaction is told the state, the reason and the
world rather than merely being refused. No structured output the gate collected
contains a recovery credential.

Not covered headless: rollback actually restoring the begin fingerprint through
the MCP path. Background Blender cannot verify a rollback at all — its own gate
asserts that — so the rollback here proves the ownership boundary instead, by
failing `UNDO_UNAVAILABLE` rather than `TRANSACTION_ORPHANED`. The restoration
claim belongs to an interactive run.

### Deterministic asset truth

`tests/blender/truth_geometry.py` and `tests/blender/truth_spatial.py`
(headless). Every defect is built by actually damaging a mesh through BMesh
rather than through RoboVision's own mutation path — a validator proved against a
hand-written expectation is only proved against the author's idea of the defect,
and a defect built with the tool under test would show only that the two agree.

Two measurements were wrong before these gates existed and both were found by
running them:

- **manifoldness and openness were one check.** Blender's `edge.is_manifold` is
  also false for a boundary edge, so an intentionally open surface failed
  `geometry.manifold` no matter what the caller declared. They are separate
  invariants now: non-manifold means topology no surface can have, and openness
  is the boundary invariant's business alone.
- **the degeneracy fixture was not degenerate.** Collapsing a single vertex of a
  cube produces zero-length edges and *no* zero-area face, because the quads
  either side merely become triangles. A gate written around that would have
  passed a mesh with a real hole in its topology.

The gates pin: a clean cube fails nothing and leaves nothing unmeasured; one hole
counts as one hole; a declared-open mesh still reports the hole and is no longer
a defect; a flipped face is found from the topology rather than from a guessed
viewpoint; a uniformly reversed solid reads as inside-out and *not* as
inconsistently wound; a floating island is a floating island; two boxes passing
through each other are found and an opted-out search is reported as unmeasured
rather than as none-found; a scaled object is measured in world space beside its
local extent, in a named coordinate contract; a size gate exists only where a
brief declared one; a pivot outside its geometry is a placement defect and not a
geometry defect. Measuring three times moves neither the revision, the
fingerprint, the object count nor the journal epoch, and measuring identical
state twice yields the same certificate id while any real change yields a
different one. Comparison refuses across a changed world, a changed coordinate
contract, different subjects or different measurement kinds, and does **not**
refuse across the moved revision and fingerprint a correction is supposed to
cause.

See [ASSET_TRUTH.md](ASSET_TRUTH.md) for the contract and for what v0
deliberately does not yet measure.

`tests/blender/truth_coverage.py`, `truth_reference.py` and `truth_pattern.py`
(all headless — the visibility test and the silhouette are raycast rather than
rendered, so no graphics context is involved and none is claimed).

The coverage fixture is a slab on a wider base, so the slab's underside is real,
front-facing from below, and occluded from every direction that could see it. Two
views from above report 66.6% unobserved; the full 42-view sphere still reaches
only 88.9%, and the same slab with nothing beneath it reaches 100% — which is
what proves the shortfall is occlusion rather than a sampler that cannot look
down. The recommended next view is checked three ways: its predicted gain matches
the measured gain exactly, no other candidate would have done better, and a fully
observed asset is recommended nothing.

The reference gate generates its references from known geometry so the expected
answer is exactly knowable. Identical geometry scores IoU 1.0 and contour
distance 0.0. A 60% wider box reports 61.8% excess and 0% deficit, localised to
the left/right sectors with zero vertical error. An asset stretched along the
front camera's own view axis scores 1.0 from the front and 0.667 from the side —
the case a single-view metric gets catastrophically wrong.

The pattern gate breaks an eight-fin array one property at a time. A missing fin
fails the count with every remaining gap still exact. A displaced fin fails
spacing and nothing else, and passes when the declared tolerance covers it. A
rotated fin fails orientation and nothing else. An enlarged fin fails dimensional
consistency and nothing else.

Three metric-definition defects were found by running these rather than by
review, and all three are recorded in ASSET_TRUTH.md: reference framing that
absorbed proportion error, a radial gap set that ignored the wrap-around, and
member extent read from a world-aligned box so that rotation registered as
resizing.

### Locality and the correction evaluator

`tests/blender/truth_locality.py` (headless) declares a blast radius and then
breaks it one way at a time: the target alone changes and passes; a protected
object is moved, and separately deleted, and both are protected changes; a helper
object is created and left behind; a dependency is touched, failing when
undeclared and passing when declared — with the same measured change count both
times, so the declaration changed how the edit was judged rather than what was
measured. A declaration naming an object as both target and protected is refused,
as is one with no targets at all.

`tests/blender/truth_correction.py` runs in an **interactive Blender** —
`xvfb-run` in CI — and asserts that it is not in background before it starts.
This is the one gate that cannot be faked headlessly: `ed.undo` there reports
FINISHED and restores nothing, so a rejected branch would leave the bad candidate
in the scene and the gate would pass anyway.

Six branches, each a full Q0 → transaction → candidate → Q1 → evaluate →
commit-or-rollback cycle against a box that is 60% too wide in its front view:

| candidate | decision | cause |
| --- | --- | --- |
| scale back to the reference | accept | committed, front excess 0.577 → 0.0 |
| scale part-way (0.115 improvement, 0.4 demanded) | reject | insufficient_target_improvement |
| fix the width and extrude a face in place | reject | failed_invariant: no_degenerate_faces |
| fix the width and deepen the side by 40% | reject | protected_regression on the *side* certificate |
| fix the width and nudge a protected neighbour | reject | locality_violation: protected_unchanged |
| require an invariant nothing measured | indeterminate | invariant_missing |

Every rejected and indeterminate branch rolled back and reproduced the begin
fingerprint exactly. Two of them are the cases a weaker design would wave
through: the third improves its declared objective and wrecks the mesh
invisibly — the extruded face is zero-area and changes no silhouette — and the
fourth improves the front view while regressing a metric of the *same name* on a
view the front camera cannot see.

One fixture was strengthened after inspecting its own output: a 1.6→1.58 nudge is
sub-pixel at the mask resolution and improved the metric by exactly nothing, so
it proved only that an unmeasurable change is rejected. The demanded improvement
was raised instead of the step shrunk, so the rejected candidate is now a real,
measured 0.115 improvement against a 0.4 requirement.

One evaluator defect was found while writing this gate: metric lookup searched
every certificate and returned the first match, so a policy naming
`reference.macro.excess_fraction` with front and side certificates both present
would silently judge whichever came first. Policies now name the certificate, and
an unqualified ambiguous name is `indeterminate` rather than a guess.


### Readiness, and the pins an external agent has to discover

`tests/blender/health.py` (headless) covers what `system.health` must keep
apart. The value of a readiness report is entirely in the independence of its
answers, so each case moves one thing and checks the others did not move with
it: an uncertain journal — produced by moving the frame under a missed
notification, which is a change the host can see and genuinely cannot attribute
— degrades `incremental_changes` while `semantic_observation` stays ready and
`mutate` is untouched; a transaction owned by another connection blocks mutation
with `transaction_active_foreign` and leaves observation alone, and the same
connection's own `object.create` is refused `TRANSACTION_FOREIGN`, so health and
dispatch cannot disagree; an orphan blocks a new correction and asks to be
adopted without the host ever suggesting the caller could do the adopting.
Background Blender reports `visual_observation` blocked for `background_mode`
beside a ready `mutate`, and the mutation health promised is then performed. It
also blocks `begin_correction` for `verified_rollback_unavailable`, and the gate
proves that is the right mechanism to name: the `transaction.begin` still
succeeds and the rollback that would reject a candidate fails
`UNDO_UNAVAILABLE`. A correction is transactional, so an editor missing the
reject branch has no correction capability rather than a weakened one — while
`mutate` and `semantic_verify` stay ready beside it. Three consecutive health
calls move neither the revision, the fingerprint, the object count nor the
journal epoch.

`tests/blender/public_flow_gate.py` (headless, over a real socket) runs
`tests/public_flow.py` — the same module Unity runs, which knows nothing about
either editor — through the public `HostSession`. It proves an external agent can
read the world incarnation, coordinate contract, units, state domain, authored
revision and journal cursor out of `system.hello`, take an authoritative
observation, and issue a fully pinned autonomous begin and mutation without
importing a host constant; that a guessed coordinate contract is refused
`COORDINATE_CONTRACT_CHANGED`, which is what makes reading the real one worth
anything; that ownership is published as `ownership = connection` and
`orphan_requires_adoption = true` rather than as prose; and that health runs on
the session's existing connection rather than opening a second one.

### Acknowledgement loss

`tests/blender/ack_loss.py` (headless) covers the window every recovery
guarantee is actually about: the host has applied a request and its response
never reaches the caller. Reaching that state by editing private state
afterwards would assert the edit rather than the behaviour, so the transport has
two deliberate test seams — drop a response after the handler ran, or drop a
request before it does — and the gate drives the public session helpers across
both.

Four losses, four questions. A **lost begin** must not destroy the credential or
its association: the secret and its correlation handle are persisted before the
request goes out, the orphan reports the handle, and the reconnecting session
binds and adopts. A **lost adoption that applied** must leave the client knowing
its replacement is current — the host's recovery generation reads 1, the client
promotes, and the re-adoption succeeds. A **lost adoption that never applied**
must leave the original current — the generation reads 0, nothing is promoted,
and the original still works. A **lost terminal reply** must be resolvable by
reading, through the surface an agent actually has: both a discard and a commit
are dropped after the host applied them, and the public status path returns the
finished record — the discard's reason, the commit's begin and final
fingerprints — with the object count asserted unchanged and the credential
released. Rollback is not among them: background Blender cannot verify a
restoration, and inventing that claim here would be worse than omitting it.

Credential reconciliation itself is a state machine, and
`tests/test_session_credentials.py` exercises it without a host, including the
host generation running ahead of anything the session pended. That value cannot
be produced on demand by a live host and is exactly where an inequality would
have promoted a secret with no basis; it raises `CREDENTIAL_STATE_DIVERGED` and
promotes nothing.

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

## Artist Loop strict delivery

`tests/blender/artist_loop_delivery.py` (headless) pins the v0.1 correction
driver. v0 opened a fully pinned autonomous transaction and then sent the
individual correction operations through plain low-level calls, so the pins were
checked once, at begin — a world reload, a unit change or a foreign edit landing
between the second and third mutation of a correction would have been executed
straight through.

The gate delivers a three-mutation correction and asserts that each operation
carried its own `expected_world`, `expected_coordinate_contract`, an
`if_revision` equal to the revision the *previous* response reported, a distinct
persisted `idempotency_key` and an `attempt`.

| deliberately stale pin | result | scene afterwards |
| --- | --- | --- |
| revision one behind | `STALE_REVISION` | unchanged, fingerprint equal |
| a world that is not this one | `STALE_WORLD` | unchanged, fingerprint equal |
| another coordinate contract | `COORDINATE_CONTRACT_CHANGED` | unchanged, fingerprint equal |

Each refusal is followed by a fingerprint comparison, because an error that
arrives after the side effect is not a refusal. A redelivery of one logical
operation — same index, same recipe, same key, `attempt=2` — is replayed by the
host rather than applied a second time.

Fail-closed recovery is covered host-independently by
`tests/test_artist_loop_fail_closed.py`: an exception in the driver's own
orchestration after begin rolls back, adopts and rolls back where the connection
was lost, reads the terminal state where it cannot, reports `unresolved` rather
than claiming safety, and never commits.

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
