# RoboVision protocol 1.0

The host transport is newline-delimited UTF-8 JSON over loopback TCP by default. Each line is one request or response. The protocol layer is independent of MCP.

## Request

```json
{"rv":"1.0","id":"uuid","method":"object.transform","params":{"object":"b3d:...","location":[1,2,3]},"if_revision":41}
```

`if_revision` is optional for reads and strongly recommended for mutations.

A mutating request may also carry `idempotency_key`, `attempt`,
`expected_world` and `expected_coordinate_contract`. Together they make a retry safe: the key names the intended
side effect, the attempt counter tells the host this is a redelivery so it
refuses to guess when it has no record, and the expected world stops a retry
being reinterpreted in an editing context it was never planned against. A
replayed response carries `replayed: true`. See CROSS_EDITOR_STATE.md §7.

A mutating response carries `outcome`: `applied` when authored state moved,
`noop` when the command succeeded and left it exactly as it found it. `revision`
advances only for `applied`, because it names authoritative scene state rather
than commands executed, and the journal and the revision must never disagree
about whether anything happened.

Authored state is the distinction that decides both. RoboVision's own
bookkeeping — an object being re-addressed, an identity repaired — is
control-plane, not something an author wrote, so it moves neither `outcome` nor
`revision`. It is not silent either: it is journalled as `IDENTITY_UPGRADED` or
`IDENTITY_REPAIRED` with the basis for the claim, because the agent's valid
address for an object may have changed even though the scene did not.

Every response carries `state_domain` and `consistency` — every response,
including failures. That was written before it was true: measured, no error
response on either host carried either field, so the contract described the
success path and claimed all of them. A failure still happened in a state
domain, and once a method resolves it still went through a tool with a declared
consistency class; before that point the class is `unknown`, which is reported
rather than defaulting to the strongest one. An audit test on each host asserts
this across representative successes and failures, so a field cannot come to
exist on only half the answers again.

`state_domain` says which universe the state in
it came from. `authored` is the scene as authored. `play_runtime` means a Unity
editor is playing: the objects reported are runtime instances of the open
scenes, discarded when play mode ends, and the `revision` — always the authored
one — versions none of them. `scene.describe` additionally reports `playing` and
`transitioning`. There is no runtime revision, because nothing needs one yet.

Authoring is an Edit Mode operation. Mutating and transaction-control methods
are refused during play mode with `PLAY_MODE_MUTATION_REFUSED`, retryable, since
leaving play mode makes them possible again. The alternative — the same
`object.create` meaning "author a scene object" in one mode and "spawn something
ephemeral" in the other — was measured doing exactly that, reporting
`outcome: noop` about a runtime object it had really created and which then
disappeared.

Every response also carries `consistency`, saying what the revision it reports is
worth: `authoritative` means the host re-read the scene for this call, so the
state, the revision and the journal position describe one moment; `notified`
means the answer reflects the last editor notification and may lag a change the
editor never announced; `independent` means the answer does not depend on scene
state. The catalog publishes each method's class as `reads`.

## Success

```json
{"rv":"1.0","id":"uuid","ok":true,"revision":42,"consistency":"authoritative","state_domain":"authored","outcome":"applied","result":{},"timing_ms":1.72}
```

## Failure

```json
{"rv":"1.0","id":"uuid","ok":false,"revision":42,"consistency":"authoritative","state_domain":"authored","error":{"code":"STALE_REVISION","message":"scene changed","retryable":true,"data":{"expected":41,"actual":42}}}
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
- `STALE_WORLD`
- `JOURNAL_REPLACED`
- `PLAY_MODE_MUTATION_REFUSED`
- `AMBIGUOUS_TARGET_SCENE`
- `TRANSACTION_FOREIGN`
- `TRANSACTION_ORPHANED`
- `TRANSACTION_ABANDONED`
- `TRANSACTION_FINISHED`
- `TRANSACTION_ADOPTION_REFUSED`
- `TRANSACTION_RECOVERY_UNCERTAIN`
- `IDEMPOTENCY_MISMATCH`
- `INDETERMINATE`
- `IN_PROGRESS`
- `SEED_REQUIRED`
- `CONTRACT_VIOLATION`
- `COORDINATE_CONTRACT_CHANGED`
- `SESSION_LOST`
- `EPOCH_SUPERSEDED`
- `SEQUENCE_TOO_OLD`
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
  fingerprint was restored, including how many undo steps that took, and the
  operation's own error payload moves to `data.operation_error_data` beside it.
  That happens for a refusal that changed nothing as much as for a failure that
  did, so a client reading its own payload must look in both places;
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
{"name":"mesh.bevel","mutating":true,"evidence":false,"requires_ui":false,"reads":"authoritative","stability":"alpha"}
```

Clients must not infer support from a method existing on another host.

## Evidence references

Visual operations return an `artifact` record containing a local path initially. A transport-neutral artifact service will later let MCP and remote adapters convert this into native image/resource forms without changing the host operation contract.
