# Unity acceptance evidence

The Unity half of [GATES.md](GATES.md), split out so neither file grows past
reading length. Gate numbering and section titles are unchanged, so existing
references still resolve.

One blocker applies to everything below and is never quietly discharged:
SceneView pixel correspondence needs a windowed editor. A CI `unity-editmode`
job that reports success without a configured licence is a **skip**, not a pass,
and is never runtime evidence.

The Unity 6000.0 floor is no longer among them. It is executed now, not merely
compiled — see *Runtime floor* below.

### Unity reconciliation

`Tests/Editor/RoboVisionReconciliationTests.cs` covers the Unity port of the one
reconciliation path and the read-consistency policy, in the EditMode suite.

These tests run with the host's editor subscriptions not installed, which is the
missed-notification case permanently rather than a shortcut: a change made
directly to the scene is never announced, so an answer is current only if the
call that produced it re-read. The suite asserts that an authoritative read finds
such a change and advances the revision, that a cheap read deliberately does not,
that the pre-mutation resync still catches one and refuses a stale `if_revision`,
that a mutation which changes nothing reports `outcome: noop` and leaves the
revision alone, that a rejected mutation does the same, and that the set of
non-authoritative tools is exactly the reviewed list.

### Unity change journal

`Tests/Editor/RoboVisionJournalTests.cs` covers the ported journal against the
shapes of change Unity reports differently, all of which have to reach it through
the same authoritative reconciliation: an Undo-recorded editor edit, a hierarchy
create, delete and reparent, a component property, a prefab instance override, a
direct change nothing announced at all, and a burst of RoboVision mutations
inside one editor update with no tick between them.

The refusals and the silences get the same weight. A broad scene-dirty signal
carries no object information and must not manufacture history. An agent mutation
followed by its own next-frame notification must not be journalled twice. A no-op
produces no revision and no event. A cursor from a replaced world is refused with
`STALE_WORLD` — asserted with both sides at epoch 1, since epoch numbers collide
across worlds — one past retention with `SEQUENCE_TOO_OLD`, and an impossible,
malformed or legacy-parameter cursor with `INVALID_PARAMS`, each carrying
`current_cursor`. Transaction contamination is still detected after the refactor.

### Editing context, journal identity and identity transitions

`Tests/Editor/RoboVisionWorldTests.cs` and the reload tests in
`RoboVisionLifecycleTests.cs` pin what a world is, after probing what the host
actually did. The probe is the reason the boundaries moved: opening an **empty**
additive scene rotated the world incarnation, reset the revision from 2 to 0
and refused the client's cursor — for an ordinary edit inside the same stage.

What the probe measured, and what the tests now assert:

- an additive open or close, and an active-scene switch, keep the world. An empty
  additive scene moves the fingerprint with no object difference at all, which is
  exactly the case that used to cost the journal its certainty; it is
  `SCENE_LOADED` now, and the client's cursor still resolves
- entering a Prefab Stage, leaving it, and swapping one for another are each a
  different world. Not deducible from the loaded scene set: the main stage's
  scene stays loaded behind an open Prefab Stage and comes back with the same
  handle, so the stage itself is part of what identifies the world
- a Single-mode load replaces it, and its cursors and snapshots are refused
- a domain reload keeps the world when the context read back out of the editor
  matches exactly, and replaces the journal regardless. The stale cursor is
  refused as `JOURNAL_REPLACED` with the new journal already past the sequence
  number it names — the equal-counter case, asserted rather than assumed
- an editor process restart mints bridge, world and journal again and refuses the
  dead process's cursor with `STALE_WORLD`, proven across two real editor
  processes by `tools/unity-restart-gate.sh`

Two defects surfaced while proving the reload contract, both measured:

- **play mode was being read as authored state.** Entering it instantiates the
  open scenes and gives their objects runtime addresses; the host took that as
  the scene, advanced the revision and lost certainty over a scene nobody
  touched. Play mode reads are now answered and never become the authored
  baseline
- **a session handle was a bare counter.** It restarts at 1 in a rebuilt domain,
  so a client holding `unity:session:1` could be handed a different object after
  a reload — the stale handle failing was an accident of how high the counter had
  climbed. Handles now carry the scope of the domain that issued them, and the
  restart test asserts a reissued handle cannot collide with a retired one

Saving is where identity transitions became visible, and the test that pins it
went through four versions. The document signature first included the scene path,
so a save would have rotated the incarnation; it is handles now. The scene's
`name` and `path` and each object's `scene` were hashed, so a save looked like
every object had changed; they are reported and not hashed. Neither was the whole
cause: in Unity an unsaved object has only a session handle, and saving is what
earns it a durable `GlobalObjectId`, so a save genuinely does change how an
object is addressed — unlike Blender, where identity is durable from the start.

That was pinned as a known gap reporting `OBJECT_DELETED` plus `OBJECT_CREATED`.
It is now `IDENTITY_UPGRADED`, and it is allowed only because it can be proven:
the retired session handle still resolves to the live object, and that object
reports the new id. The event carries `previous_id`, `id` and the basis, and the
scene revision does not move, because re-addressing an object is the host's
bookkeeping rather than an authored edit.

Blender's instance of the same problem is closed the same way, in
`tests/blender/journal_identity.py`: deleting `_robovision_id` is repaired from
the session owner registry and reported as `IDENTITY_REPAIRED` with the id
unchanged, a deliberately wrong value is overwritten back, a duplicate copy is
still separated and reported as a new object, and a property removed from the
file itself before a reopen mints a new id and claims **no** continuity — because
there is none left to prove.

One existing test changed with them: `RevisionAdvancesAcrossMutations` read the
scene revision off the host before any dispatch and expected it to keep climbing
across the scene swap its own setup performed. The revision resets with the world
incarnation, so the assumption was what was wrong; it reads through a dispatch
now.

## Gate 4 — Unity parity

Unity host passes the equivalent observe/mutate/verify/rollback loop using `GlobalObjectId`, `SerializedObject`/Undo/Prefab semantics, SceneView capture and Play Mode verification.

`tools/unity-testbed.sh` generates a throwaway project that installs the package
through the Package Manager with a `testables` entry — the way a consumer
does — and runs the package's EditMode tests in a real Unity Editor. The
manifest is written by hand rather than via `-createProject`, because a template
project pulls in a few dozen extra packages and Bee then runs a compiler process
per assembly.

### What a live Editor has proven

At the C2 checkpoint: 72 tests, 68 passing, 4 skipped, exit 0, across three
consecutive runs on **Unity 6000.6.0f1** (Windows). Left as recorded — it
describes a smaller suite than today's, not a different outcome; the current
counts, on both editors, are under *Runtime floor* below.

- the package is compiled and loaded by Unity, is reported by
  `PackageInfo.FindForAssembly`, and its host singleton is reachable
- discovery, capability enumeration and prefix filtering; typed errors for
  unknown methods, protocol mismatch and malformed requests
- snapshot fingerprint stability, create/inspect/mutate/delete, hierarchy
  reparenting, component add/list/remove
- `SerializedObject` writes reaching the real component and reading back,
  `m_Script` refused, unknown paths `NOT_FOUND`, asset-backed references
  reported as `ObjectReference`
- stale `if_revision` refused with the object unmoved; re-observing makes the
  same mutation acceptable
- transaction begin/commit; rollback restoring both a created object and a
  mutated transform and a serialized property; bystander objects untouched
- a failed mutation leaving no partial state; an out-of-band edit blocking
  automatic rollback until forced; editor undo/redo visible to the host
- prefab instances, overrides not leaking into the asset, nested prefabs,
  Prefab Stage contents, asset vs instance distinction
- the real TCP transport: framed request/response, 20 sequential calls on one
  connection, typed errors over the wire, malformed traffic refused with the
  host still serving, two clients served independently — and the shipped
  `robovision.client.RoboVisionClient` creating a GameObject in the Editor
- a Play Mode round trip, which reloads the scripting domain: the host answers
  afterwards, a `GlobalObjectId` still resolves, a `unity:session:*` handle from
  before the reload fails with `NOT_FOUND` rather than addressing something
  else, and the listener is released and rebindable

### Play mode, and where a mutation lands

Two defects found by probing before changing anything, both reproduced first:

- **`object.create` ran in play mode.** It really did create a runtime
  GameObject; reconciliation correctly refused to make a runtime read the
  authored baseline, so the response reported `outcome: noop` about a mutation
  that had visibly happened — and the object evaporated on exit. Authoring
  mutations and transaction control are refused in play mode now with
  `PLAY_MODE_MUTATION_REFUSED`, retryable, because leaving play mode makes them
  possible again. The same verb must not mean "author a scene object" in one
  mode and "spawn something ephemeral" in the other; runtime manipulation, if it
  is ever wanted, needs its own tool family and its own state domain.
- **A play mode read was indistinguishable from an authored one.** It carried
  runtime objects and the authored revision, which versions none of them. Every
  response carries `state_domain` (`authored` or `play_runtime`) now, and
  `scene.describe` also reports `playing` and `transitioning`. No runtime
  revision was invented: nothing has a use for one yet.

`Tests/Editor/RoboVisionPlayModeTests.cs` asserts the refusal, that a refused
mutation created nothing, that the authored revision and the journal's certainty
survive a round trip untouched, and that the domain flips back on exit.

Where a mutation lands was the third: `object.create` called `new GameObject`,
which goes to the active scene, and which scene is active is editor-control
state that changes outside the authored journal. Measured — after a human
switched it the journal reported zero events, and the next create landed in the
other scene. `object.create` takes an explicit `scene` now: required when more
than one is open, refused as `AMBIGUOUS_TARGET_SCENE` with the candidates and
which one the editor favours, and inside a Prefab Stage the preview scene is the
only target, which also fixed objects being created into the main stage where
the host could not see them. Hashing `active` back into authored state would
have made an editor-control change look like an edit, and was not done; whether
active-scene changes belong in the control-plane sequence owed for identity
events is left to the audit work, where that sequence gets designed.

### Transactions: ownership, world binding and recovery

Probed before anything was written, and what it found was worse than a missing
feature. A transaction stored no world. After a Single-mode load it stayed
`active` in `system.hello` with a begin fingerprint from a world that no longer
existed, accepted a further mutation, and `transaction.commit` **succeeded** —
collapsing undo groups in a universe the checkpoint never described. Entering a
Prefab Stage, leaving one and swapping one for another all did the same.
`RoboVisionTransactionLifecycleTests.cs` now asserts that each of those ends the
transaction as `abandoned` with reason `world_replaced`, and that a later commit
or rollback is told that rather than `NO_TRANSACTION`.

Survival across a domain reload was measured in both directions rather than
assumed, because persisting metadata and calling it survival proves nothing:

- **saved scene:** reverting to the transaction's undo group after the reload
  really did restore the begin fingerprint. So the transaction comes back
  `orphaned`, no connection owns it, and the token issued at begin adopts it —
  after which the rollback reproduces the checkpoint it promised
- **unsaved scene:** the reload re-addresses every object, so the checkpoint
  names identities that no longer exist. Undo removed the right objects and the
  begin fingerprint still could not be reproduced. That comes back
  `recovery_uncertain`: not adoptable, not rollback-capable, and honest about it
  — `transaction.discard` is the only outcome offered

A process restart reaches neither branch, proven across two real editors by
`tools/unity-restart-gate.sh`: SessionState dies with the process, the
transaction is not reported open, its id cannot be committed or adopted with the
token that opened it, and a newly minted transaction cannot collide with it.

Ownership over real sockets is in `RoboVisionConcurrencyTests.cs`, which replaced
a weaker rule it used to assert — any client could finish an orphan once the
owner's socket dropped. That was convenient and wrong; it treated a dropped TCP
connection as authorization. The editor still does not stay wedged, because the
owner holds a token and anyone may discard; what is gone is finishing someone
else's edit without proving anything.

### Invocation identity, and records that outlive the bridge

Ported by behaviour rather than translated. The five identifiers are the same
five — request id, idempotency key, attempt, recipe hash, seed channels — and
none is derived from another. The frame deliberately is not the same:
`rvframe:unity_y_up_left_handed_metres` against Blender's
`rvframe:blender_z_up_right_handed_metres`. Pretending the two hosts share a
frame would make every cross-editor claim quietly wrong, so each publishes its
own and a request pinning the other one is refused
`COORDINATE_CONTRACT_CHANGED` rather than executed here.

Unity has no project-level unit scale — no equivalent of Blender's
`scale_length` that a human can move between an agent's observation and its
mutation — so the contract is stable within a project. It is still pinned and
still checked, because what it catches across two editors is larger than what it
catches within one.

`RoboVisionIdempotencyTests.cs` covers the delivery model: a redelivered
mutation replays instead of authoring a second object; the replay reports the
original execution separately from the envelope, so a client can never conclude
the operation ran at the revision its retry happened to arrive at; the same key
for a different computation is `IDEMPOTENCY_MISMATCH`; a retry of a key this host
never saw is `INDETERMINATE` rather than executed; a failure whose automatic
recovery proved the pre-operation fingerprint was restored leaves the key
eligible to run again; a stochastic tool refuses to invent its own randomness and
names the channel it needs; a registry that would accept seeds on an exact tool,
a seeded tool with no channels, a side-effecting tool with no duplicate policy,
or a duplicate policy on something with no side effect, refuses all four; and the
operation ledger records the intent before the outcome and never contains a
recovery secret.

**The evidence unique to this phase** is in `RoboVisionLedgerReloadTests.cs`. A
domain reload destroys every static in the package while the editing world stays
exactly where it was — the case the durable records exist for, and one Blender
cannot produce. The bridge id rotates, the world incarnation does not, the
resumed bridge opens the same ledger file, and a redelivery of a key the previous
bridge reserved is replayed rather than applied a second time.

The direction of that argument is asserted, not assumed. World continuity is
established by reading the editor back and matching it exactly; only then may the
ledger be adopted. `TheLedgerIsNotEvidenceThatTheWorldIsTheSame` discards the
stashed world memory and changes nothing else: the scene is untouched and the
records are still on disk, and the rebuilt bridge mints a new world, opens a new
ledger and answers a retry `INDETERMINATE`. A replaced world does the same. A
ledger that could authorise replaying its own contents would be circular, and
this is where that is either true or it is not.

### Acknowledgement loss

`tools/unity-ack-loss.sh` covers the window every recovery guarantee is actually
about: the host has applied a request and its response never reaches the caller.
Reaching that state by editing private state afterwards would assert the edit
rather than the behaviour, so the transport has two deliberate seams — drop a
response after the handler ran, or drop a request before it does. The tool that
arms them lives in the package's *test* assembly, which a consuming project never
compiles: a shipped editor has no way to make the host stop answering.

The driver is the public Python `HostSession`, unchanged — the same object the
MCP adapter holds. That is the point of running it here rather than writing a C#
equivalent: the public client is meant to be host-agnostic, and this is where
that claim is tested against Unity. Seven windows:

| window | question | result |
| --- | --- | --- |
| begin, applied | does the credential survive and still find its transaction? | `SESSION_LOST`, credential held before the request went out, orphan found by its handle, adopted |
| begin, never applied | is the client honest that there is nothing to reclaim? | credential kept, `recoverable_transaction()` is `None`, the host opened nothing |
| adopt, applied | does the client know its replacement is current? | host generation 1, replacement promoted, re-adoption succeeds |
| adopt, never applied | does the client know its original is still current? | host generation 0, nothing promoted, the original still proves ownership |
| commit | is the outcome read rather than re-applied? | `committed`, with begin and final fingerprints, object count unchanged, credential released |
| discard | the same, where the proof is a reason | `abandoned`, with its reason, object count unchanged, credential released |
| mutation | does the same key replay instead of authoring twice? | applied by the host, `replayed` on redelivery, no second object |

Rollback is deliberately not among them, for the same reason it is not on the
Blender side: what a lost rollback reply would have to prove is a restoration,
and that claim belongs to the tests that actually verify one.

The credential itself is precommitted. The client generates a 32-byte secret and
sends only its SHA-256 verifier, with no salt — a salt would make the verifier
uncomputable by the client, which is exactly what precommitment needs. A
non-secret correlation handle is persisted before the request goes out, and the
recovery generation is counted so an ambiguous adoption becomes a deterministic
lookup rather than a guess. Nothing here authenticates: only the secret does.

### Autonomous invocation

`RoboVisionAutonomousTests.cs`. Low-level delivery stays permissive, so an
operator at a console can still poke the host. Under `contract: "autonomous"` a
mutation must supply `expected_world`, `expected_coordinate_contract`,
`if_revision`, `idempotency_key` and `attempt`, and is refused
`CONTRACT_VIOLATION` naming exactly what is missing. An observation-bound call
that never mutates — `transaction.begin` — must supply the first three and is not
asked for the last two, because only a mutation can be applied twice.

Observation binding is a property of the tool rather than a privilege of the
autonomous path: `transaction.begin` against a revision that has already moved is
refused `STALE_REVISION` with no contract declared at all. A checkpoint taken
from a scene the caller never planned against is a rollback target nobody chose.

### Defects this gate found

- **a cross-host defect the port exposed.** Blender's invocation ledger had a
  `note_transaction_outcome` nothing ever called, and the Blender gate that
  asserted the tombstone wrote the outcome by hand first — so it proved its own
  edit rather than the host's behaviour, and a real client's retry after a
  transaction ended would have been answered with no outcome at all. Writing the
  Unity equivalent is what surfaced it. Both hosts now report a terminal
  transaction to the ledger, and both gates assert what the host recorded.
- `transaction.status` arrived as a non-authoritative tool and the read-consistency
  audit refused the suite until it was reviewed in — the audit working as designed,
  on both hosts
- a request with no `id` was accepted, because the id was defaulted before the
  emptiness check ran
- `if_revision` was validated against a possibly stale revision, and an
  out-of-band edit during a transaction went unnoticed; both read through a
  dirty flag fed by notifications, while the transport dispatches up to eight
  requests per editor update
- with a Prefab Stage open the host described an empty project
- `viewport.capture` refused an editor whose Scene view had never been focused

### Editor restart and package re-resolution

`tools/unity-restart-gate.sh` launches two headless editors in sequence against
one generated project. Between them it verifies the first process is gone and
its port is no longer listening, so a pass cannot be an artifact of the old
editor lingering. All seven checks pass, and pass again after `PackageCache`,
`ScriptAssemblies` and `packages-lock.json` are deleted to force a real
re-resolve: the package resolves, no listener is inherited, `GlobalObjectId`s
resolve to the same objects, pre-restart session handles return `NOT_FOUND`,
the fingerprint is byte-identical, the port rebinds, and the shipped Python
client reconnects to the new process.

### Concurrency policy

Decided rather than accidental, and asserted by six tests:

- optimistic concurrency is per-request — a client passes the revision it
  planned against and the host refuses if the scene moved, whoever moved it
- a transaction belongs to the connection that opened it; another connection's
  mutation is refused with `TRANSACTION_FOREIGN`, while reads stay open
- if the owner disconnects the transaction is marked orphaned rather than
  silently committed or discarded, and any client may then finish it
  deliberately; `system.hello` reports the owner and whether it is gone
- one connection's malformed or half-sent traffic does not disturb another

### Soak

`tools/unity-soak-gate.sh` keeps one editor and runs every cycle in it, so undo
stacks, handle tables, caches and the managed heap accumulate. It requests
script reloads at intervals and resumes in the rebuilt domain, putting the
reloads inside the soak.

200 cycles with a reload every 50, one process: every cycle restored the
baseline fingerprint, nothing leaked, the scene root count held, the revision
stayed monotonic, and no transaction was left active. Mean cycle 13.65ms, p50
12.29, p95 20.1, max 73.68; mono heap 14.8MB.

### Not proven

- **SceneView capture.** The five capture tests require a Scene view, which
  `-batchmode -nographics` does not provide. They report themselves skipped
  rather than passing vacuously. A windowed run on the development machine
  crashed the Editor through commit-limit exhaustion, so the pixel-correspondence
  assertions have not executed. Compile success is not a substitute.
- **A client reconnecting over TCP inside a coroutine that spans a domain
  reload.** Attempted and removed: it fails inside the test framework's own
  resumption rather than in the host. The stronger claim — the shipped Python
  client connecting to a brand new editor process — is covered by the restart
  gate.
- **Repeat volume at the suite level.** The EditMode suite runs three
  consecutive times; the soak covers 200 cycles but exercises one cycle shape
  rather than the whole suite.
- **Ledger retention.** Every world incarnation gets its own append-only file
  under `Library/RoboVision/ledger`, and nothing prunes them: a long editing
  session accumulates one small file per editing context entered. That is
  deliberate for now — the ledger is the smallest durable spine idempotency and
  the artist loop's experiment trace can share, and a retention policy is a
  decision to make once something needs it, not a default to guess at.

### Runtime floor

Previously recorded here as infrastructure-blocked: the Hub's editor install
path was `C:\Program Files\Unity\Hub\Editor` on a drive with 0.6 GB free
against a ~19 GB editor. That is resolved. The Hub's install path is
`D:\Unity Hub\Editor` and its download location `D:\TEMP`, both verified
before anything was run, and 6000.0.83f1 — the version the compile gate uses —
is installed there. The development editor is still on the original C: path, so
the harnesses take the binary explicitly rather than assuming one root.

The floor is therefore executed rather than inferred, and the counts are kept
apart from the 6000.6 ones rather than merged into a single number:

| editor | gate | result |
| --- | --- | --- |
| 6000.0.83f1 (declared floor) | EditMode suite | 147 tests, 143 passed, 0 failed, 4 SceneView skips |
| 6000.0.83f1 (declared floor) | acknowledgement loss | 7/7 windows, `result: ok` |
| 6000.0.83f1 (declared floor) | restart + package re-resolution | 15/15 checks, all three phases on 6000.0.83f1 |
| 6000.6.0f1 (development) | EditMode suite | 147 tests, 143 passed, 0 failed, 4 SceneView skips |
| 6000.6.0f1 (development) | acknowledgement loss | 7/7 windows, `result: ok` |
| 6000.6.0f1 (development) | restart + package re-resolution | 15/15 checks, all three phases on 6000.6.0f1 |
| 6000.0.83 (CI) | compile only | `ROBOVISION_UNITY_COMPILE_PASS` |

Each editor gets its own generated project and its own artifact directory, so
neither run's numbers can be read as the other's. At the world/identity
checkpoint the suite was 112/108/0/4 and the restart gate 11/11; at the
transaction checkpoint 118/114/0/4. The counts above are the current ones — the
suite grew by the idempotency, ledger-reload and autonomous-contract fixtures,
and by the commit-after-reload and four-transition proofs.

Both editors were confirmed from the run's own log — `Initialize engine version:
6000.0.83f1` — rather than from the path it was launched by. Compile coverage
did not discharge this and never could; it is recorded separately above because
it still catches a different class of problem earlier.

The historical figures elsewhere in this file — 72 tests, 68 passing, 4 skipped —
are from the C2 checkpoint and are left as they were. They describe a smaller
suite, not a different result.

### Identity contract

| reference | issued for | survives domain reload | survives editor restart |
| --- | --- | --- | --- |
| `unity:GlobalObjectId_*` | objects in a saved scene or asset | yes, asserted | yes, asserted by the restart gate |
| `unity:session:<scope>:<n>` | unsaved scene objects with no persistent id | no, asserted to fail `NOT_FOUND` | no, asserted to fail `NOT_FOUND` |

The scope is the loaded domain, as a whole GUID. Without it the numeric part is
a counter that restarts at 1 in a rebuilt domain, so a client holding
`unity:session:1` could be handed a different object afterwards and the
stale-handle assertion above would be passing on how high the counter happened
to have climbed. It was eight hex characters — 32 bits — behind a comment
promising a domain's scope is never reused, which 32 bits does not keep. The
collision is now constructed by hand in the test rather than waited for.

`object.inspect` reports `identity_persistent` so a client never has to infer
which kind it holds.

### Defects this gate found

- a request with no `id` was accepted, because the id was defaulted before the
  emptiness check ran
- `if_revision` was validated against a possibly stale revision, and an
  out-of-band edit during a transaction went unnoticed
- with a Prefab Stage open the host described an empty project
- `viewport.capture` refused an editor whose Scene view had never been focused
- a second client's mutation silently joined a transaction it did not open
- `scene.isDirty` was inside the hashed state, and Unity flips it
  asynchronously, so the host reported a change nobody made and refused to roll
  back with `TRANSACTION_CONTAMINATED`
- the testbed ran Unity's previously built assembly when the test assembly
  failed to compile, so results described stale code

Gate 4 is therefore **not passed**. The substrate, identity lifecycle, restart
and re-resolution, concurrency policy and 200-cycle soak are runtime-proven;
SceneView capture and the 6000.0 runtime floor are not.
