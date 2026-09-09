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

cursor := rvcursor:<journal incarnation>:<world incarnation>:<epoch>:<sequence>
```

Events carry a monotonic `sequence`, the `revision` they produced, a `type`,
affected **stable ids** (never names), a `path` where known, a `source`
(`agent` with its request id, `editor`, or `host`), and a timestamp.

A bare sequence number is not a position. Sequence restarts at every new
certainty epoch, at every new journal and at every new world, so one integer
names different moments in different histories — and every one of those counters
starts again from the same number, so no counter separates them either. Only the
incarnations do, and the cursor carries both, validated outermost scope first:

| the cursor names | response |
| --- | --- |
| an editing context that is not open | `STALE_WORLD` |
| a journal that is not running, in a world that is | `JOURNAL_REPLACED` |
| a superseded certainty epoch | `EPOCH_SUPERSEDED` |
| a sequence the journal no longer retains | `SEQUENCE_TOO_OLD` |
| a sequence ahead of the journal | `INVALID_PARAMS`; no such position exists |
| a malformed, non-string or out-of-range cursor | `INVALID_PARAMS` |

Every one of those refusals carries `current_cursor`. A client that cannot
continue needs exactly one thing to recover, it is the same thing in all six
cases, and a client with an unparseable cursor and no offered replacement has
nowhere to go but a full resynchronisation.

The two incarnations fail for reasons a client must act on differently, which is
why they are separate codes. `STALE_WORLD` means the editing context is gone:
re-observe. `JOURNAL_REPLACED` means the world is still open and the durable
references still resolve — only the history restarted, which is what a domain
reload does.

What validation establishes, precisely: the cursor is well formed, names the
world open now and the journal running in it, names the current certainty epoch,
and falls inside the retained range. It does **not** establish issuance — nothing is
signed, so a client that constructs a syntactically valid cursor for the current
world is indistinguishable from one handed the same string. That is deliberate
for a read-only journal: a forged cursor reads events its caller could already
read, while the checks that matter are about staleness, which a forger has no
reason to fake. If the journal ever gains side effects, this stops being enough.

Omitting the cursor is a bootstrap: the host says where the journal is and
returns **no events**, because a client that never held a position cannot tell a
truncated history from a complete one. Passing `cursor: null` is not the same
claim and is refused — a client whose position came back null has lost it, and
answering "nothing changed" is the exact failure this call exists to prevent.

Both editors under-report. `bpy.msgbus` does not fire for a viewport drag;
`ObjectChangeEventStream` is a per-frame view a batch operation can outrun. So
certainty is tracked as an **epoch**:

- the journal carries `epoch`, incremented only by an authoritative snapshot
- when the host cannot attribute a change, it emits `RESYNC_REQUIRED` and clears
  certainty for the current epoch
- **once cleared, certainty is sticky.** A later clean-looking notification does
  not restore trust. Only an authoritative snapshot opens a new epoch

## Read consistency

Reconciliation is not free, and not every call needs it, so each tool declares
what its answer is worth. The dispatcher enforces the declaration; a handler
never arranges its own freshness.

| class | before the handler runs | used by |
| --- | --- | --- |
| `authoritative` | a full deep read, reconciled | every mutation, and everything that presents scene state |
| `notified` | service a pending notification, nothing more | `scene.changes_since`, `system.ping`, `system.hello` |
| `independent` | nothing; the answer does not depend on the scene | `system.capabilities`, `system.method` |

The default is `authoritative`, so a tool that presents scene state and forgets
to classify itself is correct and slow rather than fast and wrong. Every response
reports the class it got, and the catalog publishes it per method.

This is a separate axis from `evidence`, which says whether a call produces a
durable artifact. Only the two capture methods are evidence-producing, while
`scene.describe` produces no artifact and still must not answer from a stale
baseline, so the two cannot be one flag.

Why it is not optional: measured with a notification missed, `scene.describe`
returned an object at its genuinely current position stamped with the scene
revision and journal cursor of the state *before* it, and journalled nothing.
Either half being stale is survivable. The pair is not — it tells a client the
state it is looking at is already accounted for.

Journal polling is deliberately in the cheap class. Reconciling there would make
every poll a deep read and would make polling itself the thing that discovers
changes, confusing *reporting* a position with *establishing* one.

Both hosts implement this. Unity's classes and defaults are the same, including
the rule that a mutating tool cannot declare anything but `authoritative`.

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

### The same path in Unity

Unity had the same three near-identical bodies — `RefreshDirtyState`, `Resync`,
`AcceptOwnMutation` — and they are now one `RoboVisionReconciler` with the same
steps in the same order: capture an authoritative read, compare the baseline
fingerprint, advance the revision only if state moved, derive `applied` or
`noop`, diff old to new, attribute, journal it or lose certainty, replace the
baseline. The journal, cursors, epochs and refusals are the same contract, so an
agent driving both editors does not have to learn two histories.

The temptation to do otherwise is stronger in Unity, not weaker.
`ObjectChangeEvents.changesPublished` looks like a real change feed, but it
publishes undoable changes to *loaded* objects once per frame, so it is not
comprehensive, and its broad events — `ChangeScene` among them — may carry no
object information at all. Every editor notification therefore funnels into
`MarkDirty` and no further: notifications say something *may* have changed, and
reconciliation decides what did. `ObjectChangeEventStream` is sensor input to
this state machine, never the durable journal.

The same reasoning decides *which world* is open. Unity's world incarnation is
derived from a context read out of the editor — the open stage and its loaded
scenes — rather than from `sceneOpened` or stage callbacks, because identity
established by having been told has the same weakness as change detection
established by having been told. What counts as a different context, and the
probe that decided it, is §1.3 of [IDENTITY.md](IDENTITY.md).

Scenes are identified by handle, deliberately not path. A handle identifies a
loaded instance for the life of a session, which is the scope a world
incarnation has; an unsaved scene has no path at all, and including the path
would make *saving* look like loading a different world. Which file the document
is stored in is reported by `scene.describe`, where it belongs.

Saving is not an edit, and Unity needed that enforced as well as stated: the
scene's `name` and `path` and every object's `scene` field were inside the hashed
state, so saving an untitled scene made every object in it look changed. They are
reported and not hashed now, like `isDirty` before them. Scene membership
survives the change because objects are nested under the scene they belong to.

## A command that ran is not a scene that changed

Scene revision names authored scene state, not commands executed. Every mutating
command reports an `outcome`:

| result | outcome | scene revision |
| --- | --- | --- |
| authored state moved | `applied` | advances |
| succeeded, authored state unchanged | `noop` | unchanged |
| rejected or failed without changing state | — | unchanged |
| only the host's own bookkeeping changed | `noop` | unchanged, and journalled |

Journal events and the scene revision may never disagree about whether state
moved: a `noop` produces neither.

## Authored state and control-plane changes

The last row of that table is the one that needs explaining, and it is where two
measured cases used to produce a lie.

RoboVision addresses objects by ids it issues, and that addressing can change
while the scene does not. In Blender `_robovision_id` is an ordinary custom
property that anyone can delete or overwrite. In Unity an unsaved object has only
a session handle, and saving is what earns it a durable `GlobalObjectId`. In both
hosts the object diff could only report one id vanishing and another appearing —
`OBJECT_DELETED` plus `OBJECT_CREATED` — which tells an agent its world was
demolished and rebuilt when nothing in the scene moved at all.

Two events, and only two, because a third would be a claim without evidence:

| event | what changed | proof required |
| --- | --- | --- |
| `IDENTITY_REPAIRED` | nothing a client can see; the host rewrote its own record | this live object already owns that id in this session |
| `IDENTITY_UPGRADED` | the public address, from `previous_id` to `id` | the retired address still resolves to the same live object, and that object reports the new one |

Both carry a `basis`, because a restored or linked identity is worth exactly what
the evidence behind it is worth. Where there is none — a property removed from
the file itself, a handle that died with its domain, an id minted after a
reopen — nothing is linked and the delete and the create stand, which is what the
host actually knows.

Neither advances the scene revision. That counter answers *did the authored scene
move*, and re-addressing an object is the host's bookkeeping. Neither is silent
either, because the agent's valid address for an object may have changed. What is
still owed, and belongs to the audit and replay work rather than here, is a
control-plane sequence of its own, so a client can distinguish "nothing has
happened" from "nothing authored has happened" without reading events.

The same distinction decides two smaller cases. Loading or unloading a scene
inside one world is authored — the world's contents changed — and is reported as
`SCENE_LOADED` or `SCENE_UNLOADED`, which also stops an empty additive scene from
moving the fingerprint with nothing to attribute it to. Switching which scene is
active authors nothing, journals nothing, and is reported in `scene.describe`
because an agent still needs to know where its next object will land.

Unity play mode is neither. Entering it instantiates the open scenes and gives
their objects runtime addresses; leaving it discards all of that. Reads are
answered while it runs, and none of them becomes the authored baseline — before
this was enforced, a play mode round trip advanced the scene revision and cost
the journal its certainty over a scene nobody had touched.

The journal is a performance optimization for polling. It never substitutes for a
fingerprint in a proof, and transactions keep comparing deep fingerprints.

