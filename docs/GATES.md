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
| soak 500 cycles | Blender 5.1.2, Windows | pass, mean 47.8ms, max 55.0ms |
| soak 250 cycles | Blender 5.2.1 LTS, Linux CI | pass, mean 35.1ms, max 40.9ms |

The Gate 1 baseline fingerprint is identical on both platforms and versions.

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

Unity host passes the equivalent observe/mutate/verify/rollback loop using `GlobalObjectId`, `SerializedObject`/Undo/Prefab semantics, SceneView capture and Play Mode verification.

`tools/unity-testbed.sh` generates a throwaway project that installs the package
through the Package Manager with a `testables` entry — the way a consumer
does — and runs the package's EditMode tests in a real Unity Editor. The
manifest is written by hand rather than via `-createProject`, because a template
project pulls in a few dozen extra packages and Bee then runs a compiler process
per assembly.

### What a live Editor has proven

66 tests, 62 passing, 4 skipped, exit 0, across three consecutive runs on
**Unity 6000.6.0f1** (Windows):

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

### Defects this gate found

- a request with no `id` was accepted, because the id was defaulted before the
  emptiness check ran
- `if_revision` was validated against a possibly stale revision, and an
  out-of-band edit during a transaction went unnoticed; both read through a
  dirty flag fed by notifications, while the transport dispatches up to eight
  requests per editor update
- with a Prefab Stage open the host described an empty project
- `viewport.capture` refused an editor whose Scene view had never been focused

### Not proven

- **SceneView capture.** The five capture tests require a Scene view, which
  `-batchmode -nographics` does not provide. They report themselves skipped
  rather than passing vacuously. A windowed run on the development machine
  crashed the Editor through commit-limit exhaustion, so the pixel-correspondence
  assertions have not executed. Compile success is not a substitute.
- **Editor restart and package reload.** Domain reload is covered by the Play
  Mode tests above; a full editor restart and a package re-resolve are not.
  `SessionState`, which the reload tests rely on, does not survive a restart
  either, so that case needs a different harness.
- **Unity 6000.0 at runtime.** The declared floor is compile-verified in CI
  only; every runtime result above is from 6000.6.0f1.
- **Repeat volume.** Three consecutive runs, not the hundreds the Blender soak
  gate does.

### Identity contract

| reference | issued for | survives domain reload | survives editor restart |
| --- | --- | --- | --- |
| `unity:GlobalObjectId_*` | objects in a saved scene or asset | yes, asserted | expected; not yet asserted |
| `unity:session:<n>` | unsaved scene objects with no persistent id | no, asserted to fail `NOT_FOUND` | no |

`object.inspect` reports `identity_persistent` so a client never has to infer
which kind it holds.

Gate 4 is therefore **not passed**. The substrate, identity lifecycle and domain
reload behaviour are runtime-proven; SceneView capture, editor restart, the
6000.0 runtime floor and repeat volume are not.

## Gate 5 — Blender → Unity lineage

RoboVision can trace a source Blender object through export/import to Unity asset GUID, prefab and scene instance; a defect in Unity can be mapped back to its Blender source identity.

## Gate 6 — agent-grade autonomy

Long jobs, cancellation, progress, event subscriptions, scene-change streaming, artifact resources, deterministic macros, replay, audit log, policy limits, recovery after editor/domain reload, and stress/soak tests.
