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

## Gate 5 — Blender → Unity lineage

RoboVision can trace a source Blender object through export/import to Unity asset GUID, prefab and scene instance; a defect in Unity can be mapped back to its Blender source identity.

## Gate 6 — agent-grade autonomy

Long jobs, cancellation, progress, event subscriptions, scene-change streaming, artifact resources, deterministic macros, replay, audit log, policy limits, recovery after editor/domain reload, and stress/soak tests.
