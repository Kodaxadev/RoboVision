# Architecture

## 1. Control-plane split

RoboVision separates the AI-facing control plane from editor-native execution.

```text
AI / agent / MCP client
        |
        | structured RoboVision operations
        v
RoboVision client / future MCP adapter
        |
        | local versioned transport
        v
+----------------------+        +----------------------+
| Blender host         |        | Unity host           |
| editor main thread   |        | editor main thread   |
| bpy/BMesh/Depsgraph  |        | SerializedObject     |
| viewport perception  |        | SceneView/GameView   |
+----------------------+        +----------------------+
```

The host protocol is intentionally not MCP-specific. MCP is one adapter. This keeps editor semantics stable when agent transports change.

## 2. Perception-action loop

1. `system.hello` and capability discovery.
2. `scene.snapshot` and one or more perception captures.
3. Plan against explicit object IDs and scene/topology revisions.
4. `transaction.begin`.
5. Perform small deterministic mutations.
6. Inspect machine state and visual evidence.
7. Correct if necessary.
8. Validate.
9. `transaction.commit`, or `transaction.rollback` and prove the initial fingerprint was restored.

A tool returning `ok=true` only means the host operation completed. Quality claims require evidence/validation.

## 3. Editor-thread rule

### Blender

Blender's Python integration is not thread-safe. RoboVision therefore does **not** run a threaded socket server in Blender. Its socket is non-blocking and polled by `bpy.app.timers`, so transport parsing and every `bpy`/BMesh call execute on Blender's main event thread. Long-running work will use explicit jobs rather than background `bpy` access.

### Unity

Unity editor mutations are dispatched from `EditorApplication.update`. No `UnityEngine.Object` or `UnityEditor` mutation is allowed off the editor thread.

## 4. Identity model

- **Blender objects:** persistent custom property `_robovision_id` (`b3d:<uuid>`). Duplicate-ID repair runs during inspection and bridge-owned duplication immediately allocates a new ID.
- **Blender mesh elements:** indices are not stable IDs. Element references include a mesh revision; topology mutation increments it. Stale references fail.
- **Unity assets/scene objects:** use Unity `GlobalObjectId` at authoring time rather than inventing names as identity.
- **Cross-host lineage:** a later asset ledger maps source object → export artifact → Unity asset GUID → prefab/scene instance.

## 5. Revisions and optimistic concurrency

Each host exposes a monotonic scene revision. Mutations may send `if_revision`. If the editor changed since the agent inspected it, the host returns `STALE_REVISION` rather than editing unknown state.

Editor-native change notifications also mark revision state dirty so manual user edits are visible to the agent.

## 6. Transactions

Transactions are host-native where possible. In Blender Gate 1, RoboVision creates explicit undo boundaries before transaction mutations and stores a deep scene fingerprint at begin. Rollback performs only the transaction's recorded undo count and then recomputes the fingerprint. A mismatch is `ROLLBACK_INCOMPLETE`, not success.

## 7. Perception

Perception is a first-class subsystem. Captures return the image artifact plus camera/view matrices, projection state, shading mode, viewport dimensions, scene revision, and capture time. Future capture bundles add depth, normal, object-ID, material-ID, wireframe/topology and UV evidence.

## 8. Escape hatches

Arbitrary host code is not part of the default capability set. If added later it must be explicitly enabled, isolated, audited and identified as unverified execution. The normal modeling surface remains typed operations.
