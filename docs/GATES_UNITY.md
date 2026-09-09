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

### Defects this gate found

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

### Runtime floor

Previously recorded here as infrastructure-blocked: the Hub's editor install
path was `C:\Program Files\Unity\Hub\Editor` on a drive with 0.6 GB free
against a ~19 GB editor. That is resolved. The Hub's install path is
`D:\Unity Hub\Editor` and its download location `D:\TEMP`, both verified
before anything was run, and 6000.0.83f1 — the version the compile gate uses —
is installed there.

The floor is therefore executed rather than inferred, and the counts are kept
apart from the 6000.6 ones rather than merged into a single number:

| editor | gate | result |
| --- | --- | --- |
| 6000.0.83f1 (declared floor) | EditMode suite | 112 tests, 108 passed, 0 failed, 4 SceneView skips |
| 6000.0.83f1 (declared floor) | restart + package re-resolution | 11/11 checks, all three phases on 6000.0.83f1 |
| 6000.6.0f1 (development) | EditMode suite | 112 tests, 108 passed, 0 failed, 4 SceneView skips |
| 6000.6.0f1 (development) | restart + package re-resolution | 11/11 checks |
| 6000.0.83 (CI) | compile only | `ROBOVISION_UNITY_COMPILE_PASS` |

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
