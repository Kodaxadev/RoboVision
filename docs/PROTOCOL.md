# RoboVision protocol 1.0

The host transport is newline-delimited UTF-8 JSON over loopback TCP by default. Each line is one request or response. The protocol layer is independent of MCP.

## Request

```json
{"rv":"1.0","id":"uuid","method":"object.transform","params":{"object":"b3d:...","location":[1,2,3]},"if_revision":41}
```

`if_revision` is optional for reads and strongly recommended for mutations.

A mutating response carries `outcome`: `applied` when the scene fingerprint
moved, `noop` when the command succeeded and left the state exactly as it found
it. `revision` advances only for `applied`, because it names authoritative scene
state rather than commands executed — a `noop` writes no journal events either,
and the two must never disagree about whether anything happened.

## Success

```json
{"rv":"1.0","id":"uuid","ok":true,"revision":42,"outcome":"applied","result":{},"timing_ms":1.72}
```

## Failure

```json
{"rv":"1.0","id":"uuid","ok":false,"revision":42,"error":{"code":"STALE_REVISION","message":"scene changed","retryable":true,"data":{"expected":41,"actual":42}}}
```

## Core error codes

- `INVALID_REQUEST`
- `PROTOCOL_MISMATCH`
- `UNKNOWN_METHOD`
- `INVALID_PARAMS`
- `NOT_FOUND`
- `UNSUPPORTED`
- `STALE_REVISION`
- `STALE_TOPOLOGY`
- `INVALID_CONTEXT`
- `TRANSACTION_ACTIVE`
- `NO_TRANSACTION`
- `ROLLBACK_INCOMPLETE`
- `MUTATION_RECOVERY_INCOMPLETE`
- `TRANSACTION_CONTAMINATED`
- `RECOVERY_UNSAFE`
- `MESSAGE_TOO_LARGE`
- `HOST_EXCEPTION`

### Failure and recovery semantics

A mutating operation that raises is not allowed to leave partial state behind
silently. The host checkpoints before the operation and, on failure, restores
that checkpoint and reports the outcome:

- the operation error carries `data.automatic_recovery` when the pre-operation
  fingerprint was restored, including how many undo steps that took;
- `MUTATION_RECOVERY_INCOMPLETE` replaces the operation error when restoration
  could not be proved, and carries the original error in
  `data.original_error`. The caller must treat the scene as unknown and
  re-observe;
- `RECOVERY_UNSAFE` means restoration was not attempted because Blender was not
  in Object Mode. Global undo walks the active mode's own stack, so driving it
  from Edit, Sculpt or Pose mode can destroy live work instead of restoring it;
- `TRANSACTION_CONTAMINATED` means an out-of-band editor change happened inside
  the transaction. Commit and rollback both refuse until the caller passes
  `force`, so RoboVision never rolls a human's unrelated edit backward on its
  own.

## Snapshot levels

`scene.snapshot`, `scene.describe`, `scene.diff` and `object.inspect` take a
`level` of `shallow`, `standard` or `deep` (the older `deep: true|false` boolean
still maps onto `deep`/`standard`).

| level | contains | intended use |
| --- | --- | --- |
| `shallow` | identity, type, world transform, parent, visibility, mesh counts | listings and cheap polling |
| `standard` | adds modifier settings, material slots, collections, custom properties, parent inverse, topology signature, unlinked datablocks | change detection |
| `deep` | adds full geometry digest | proofs |

Anything that claims restoration is pinned to `deep`. A fingerprint is only
comparable with another fingerprint of the same level, and `scene.diff` reports
`comparable` so a caller cannot mistake a cheap comparison for a proof.

Fingerprints are deterministic across platform and Blender version: the Gate 1
baseline hashes identically on Windows/Blender 5.1.2 and Linux/Blender 5.2.1.

## Topology revisions

`mesh_revision` is derived from a signature of the mesh's structure, not from a
counter RoboVision increments. An edit made in Edit Mode, by another add-on or
by undo therefore advances it too, and a topology-indexed mutation carrying the
old `expected_mesh_revision` fails with `STALE_TOPOLOGY` instead of addressing
whichever element now holds that index. Every topology-indexed mesh mutation
requires `expected_mesh_revision`; there is no unchecked form.

## `system.hello`

Every host returns:

- protocol version
- RoboVision host implementation/version
- editor name/version
- current scene revision
- enabled capabilities
- transport/security facts
- limitations/warnings relevant to this editor/version

## Method metadata

Capabilities list methods with at least:

```json
{"name":"mesh.bevel","mutating":true,"evidence":false,"requires_ui":false,"stability":"alpha"}
```

Clients must not infer support from a method existing on another host.

## Evidence references

Visual operations return an `artifact` record containing a local path initially. A transport-neutral artifact service will later let MCP and remote adapters convert this into native image/resource forms without changing the host operation contract.
