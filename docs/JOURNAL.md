# The change journal, cursors, and sticky uncertainty

Part of the contract in [CROSS_EDITOR_STATE.md](CROSS_EDITOR_STATE.md), kept
separately because the cursor grammar, the reconciliation rule and the revision
semantics are each load-bearing on their own.

The journal exists so an agent can ask "what moved since I last looked?" instead
of re-reading the whole scene. It is a performance optimisation, and everything
below is about stopping it from quietly becoming something an agent trusts more
than it should. Silence is the dangerous direction: an empty event list means
"nothing changed", so every path that could return one while something *had*
changed is a defect, not a rough edge.

## Reading the journal

```text
scene.changes_since()        -> { cursor, epoch, certain, bootstrap: true, events: [] }
scene.changes_since(cursor)  -> { cursor, epoch, certain, revision, events[] }

cursor := rvcursor:<document incarnation>:<epoch>:<sequence>
```

Events carry a monotonic `sequence`, the `revision` they produced, a `type`,
affected **stable ids** (never names), a `path` where known, a `source`
(`agent` with its request id, `editor`, or `host`), and a timestamp.

A bare sequence number is not a position. Sequence restarts at every new
certainty epoch and at every new document incarnation, so one integer names
different moments in different worlds — and both epoch counters start at 1, so
an epoch alone does not separate them either. A cursor therefore carries the
world it was issued in, and is validated in that order:

| the cursor names | response |
| --- | --- |
| a document incarnation that is not loaded | `STALE_DOCUMENT` |
| a superseded certainty epoch | `EPOCH_SUPERSEDED` |
| a sequence the journal no longer retains | `SEQUENCE_TOO_OLD` |
| a sequence ahead of the journal | `INVALID_PARAMS`; no host issued it |

Every refusal carries `current_cursor`, so a client is always told where to
resume. Calling without a cursor is a bootstrap: the host says where the journal
is and returns **no events**, because a client that never held a position cannot
tell a truncated history from a complete one.

Both editors under-report. `bpy.msgbus` does not fire for a viewport drag;
`ObjectChangeEventStream` is a per-frame view a batch operation can outrun. So
certainty is tracked as an **epoch**:

- the journal carries `epoch`, incremented only by an authoritative snapshot
- when the host cannot attribute a change, it emits `RESYNC_REQUIRED` and clears
  certainty for the current epoch
- **once cleared, certainty is sticky.** A later clean-looking notification does
  not restore trust. Only an authoritative snapshot opens a new epoch

## One reconciliation path

Everything that moves the host's baseline — the dirty refresh, the pre-mutation
resync, accepting the agent's own mutation, an authoritative `scene.snapshot`,
recovery from a failed mutation — goes through one reconciliation: read the
current deep snapshot, compare the previous fingerprint, advance the scene
revision only if state actually moved, diff old to new, attribute it, write the
events, lose certainty where attribution is impossible, replace the baseline.

That is a correctness requirement, not tidiness. Three near-identical bodies
differed in ways that mattered: `resync` advanced the revision on a fingerprint
change and journalled nothing, so an editor edit whose notification never
arrived was absorbed into the next baseline while the journal went on claiming
certainty. Missed notifications are not hypothetical — Blender fires
`depsgraph_update_post` only when something forces evaluation, so a script that
writes a datablock and does not update leaves the host clean over a stale
baseline. An authoritative `scene.snapshot` reconciles before it answers, so it
is a genuine baseline even when the host believed nothing had happened.

## A command that ran is not a scene that changed

Scene revision names authoritative scene state, not commands executed. Every
mutating command reports an `outcome`:

| result | outcome | scene revision |
| --- | --- | --- |
| the fingerprint moved | `applied` | advances |
| succeeded, fingerprint unchanged | `noop` | unchanged |
| rejected or failed without changing state | — | unchanged |

Journal events and the scene revision may never disagree about whether state
moved: a `noop` produces neither.

The journal is a performance optimization for polling. It never substitutes for a
fingerprint in a proof, and transactions keep comparing deep fingerprints.

