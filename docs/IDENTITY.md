# Identity layers

Part of the contract in [CROSS_EDITOR_STATE.md](CROSS_EDITOR_STATE.md), kept
separately because what rotates when is load-bearing on its own and is what
every other guarantee is addressed through. Section numbers are preserved so
existing references still resolve.

## 1. Identity is layered, not one id

Collapsing lifecycle into one id loses exactly the distinctions that make stale
state detectable. Nine levels. Three of them were previously conflated, which
hid the difference between *the bridge was reloaded*, *a different editing
context is open* and *the history I was reading has restarted*.

| level | identifier | rotates when | notes |
| --- | --- | --- | --- |
| workspace | `rvws:<uuid>` | a different RoboVision project context is adopted | the larger context a set of documents belongs to |
| document / asset identity | `rvdocid:<uuid>` | a genuinely different document is authored | persisted inside the file; see §1.1 |
| host process | `rvproc:<uuid>` | the editor process starts | minted per process, never persisted |
| bridge incarnation | `rvbridge:<uuid>` | add-on disable/enable or reload, Unity domain or assembly reload, bridge restart | the loaded RoboVision runtime itself |
| world / edit-context incarnation | `rvworld:<uuid>` | a different editing context is opened; see §1.3 | one loaded editing universe, even if the bridge never reloaded |
| journal incarnation | `rvjournal:<uuid>` | the journal's history restarts, including inside a surviving world | a world can outlive the history kept about it |
| scene revision | `int`, monotonic | authored scene state changes | resets with the world incarnation |
| object / component identity | `b3d:<uuid>` / `unity:GlobalObjectId_*` | never, for saved objects | survives restart (asserted); see §11.1 |
| mesh revision | `int`, monotonic | editable mesh topology changes | stored with the mesh, survives save |
| observation | `rvobs:<uuid>` | every capture | bound to the world incarnation it was taken in |

The three that were conflated:

- **bridge incarnation** answers *is the code I am talking to the same code?*
  Reloading the add-on rotates it while the open file is untouched.
- **world incarnation** answers *is this the same editing context?* Opening a
  different one rotates it while the bridge keeps running, its sockets stay
  bound and its module state survives.
- **journal incarnation** answers *is this the same history?* A rebuilt bridge
  can wake up in the world it left and verify that it did, but its events are
  gone. Without a separate identity a cursor from before would name a world
  that is genuinely current and an epoch and sequence that both restarted at the
  same numbers, and would resolve against a history nothing kept.

They rotate independently, and an agent needs all three: a handle from before a
bridge reload is void for a different reason than one from before a scene swap,
and conflating them makes one of those errors unreportable.

The system can then answer: *this decision came from observation O, in world
incarnation D of document W, at scene revision S and mesh revision M, through
bridge B in process P.*

**Owed errors.** `STALE_BRIDGE` when a request carries an `rvbridge` that is no
longer loaded. `STALE_WORLD` when it carries an `rvworld`, a snapshot or a
cursor from an editing context that is no longer open. `JOURNAL_REPLACED` when
the world is still open but the history that issued the cursor is not.
`WRONG_DOCUMENT` when it names a document this host does not have open. All
distinct from `NOT_FOUND`, which means the object is missing from a world that
*is* current.

### 1.3 What counts as a different editing context

Measured per host, because guessing here is expensive in both directions:
rotating too eagerly voids every handle a client holds for an ordinary edit,
and rotating too late diffs one universe against another.

**Blender.** One loaded `.blend` is one world. Opening, reopening or replacing
the file rotates it. Reattaching the bridge also rotates it, and that is a
limitation rather than a rule: an add-on reload takes the module's own memory
with it and leaves nothing session-scoped to verify continuity against, so
continuity is not claimed.

**Unity.** A world is *the stage plus its members*, both read out of the editor
rather than subscribed to.

| event | world | why |
| --- | --- | --- |
| object, component or property change | same | the context did not change, only its contents |
| additive scene open or close | same | Unity's main stage *is* the currently open scenes; the journal reports `SCENE_LOADED` / `SCENE_UNLOADED` |
| active scene switch | same | reported in `scene.describe`, authored nothing, journals nothing |
| Single-mode load | **new** | nothing of the previous editing universe stays loaded |
| main stage → prefab stage | **new** | a different editing context, not deducible from the loaded scene set: the main stage's scenes stay loaded behind it with their handles unchanged |
| prefab stage → main stage | **new** | the main stage's contents survived, but nothing observed them while the prefab was open, so its history cannot be resumed |
| one prefab stage → another | **new** | a different asset, and a different preview scene |
| domain or assembly reload | **same, if verified** | the context is read back and must match exactly; the journal restarts regardless |
| editor process restart | **new** | session-scoped memory dies with the process, so continuity cannot be verified |
| play mode | same, and not read as authored state | play mode instantiates the open scenes; leaving discards that, so a play mode read never becomes the authored baseline |

Continuity within a stage is **overlap**, not equality: adding or removing a
scene keeps the world, while a load that leaves nothing of the previous set is a
replacement. Continuity across a bridge reload is **exact** equality, because
nothing was watching in between.

## 1.1 Document identity, copies and forks

**Not yet implemented, and deliberately conservative.** Persisting an id inside a
file means copies of the file carry copies of the id, so the design has to say
what happens before anything is persisted.

The document record stored in the file is `{ document_id, last_known_path }`.
Path is recorded not as identity but as evidence for the fork check below.

| event | document id | why |
| --- | --- | --- |
| Save (same path) | preserved | the same document, written again |
| Save As (new path) | **new id minted**, previous recorded as `forked_from` | the original file still exists on disk and is still that document; two live files must not share one identity |
| Save a Copy | in-memory document keeps its id; the copy on disk carries a duplicate until opened | unavoidable — nothing runs at copy time |
| filesystem copy, rename or move | undetectable at copy time | resolved on open, below |
| open, `last_known_path` equals the file's path | preserved | the ordinary case |
| open, `last_known_path` differs | **new id minted**, previous recorded as `forked_from`, host reports `document_forked` | a move and a copy are indistinguishable from the file alone, so the safe reading is "a different document" |

A move therefore costs a new id by default. That is the conservative direction:
wrongly minting a new id makes a client re-observe, while wrongly sharing one
makes two files silently the same document. A client that knows a move happened
can say so explicitly:

```text
document.claim_identity(previous_document_id) -> ok | CLAIM_REFUSED
```

**Concurrent open of two files carrying the same id** is not solved by anything
in the file. Two editor processes can each open a copy and both believe they are
that document. Detection requires coordination outside the documents — a
lock or registry entry keyed by `document_id` that a host takes on open and
releases on close, so the second host sees the id already claimed and reports
`DOCUMENT_ID_IN_USE` with the holder's process identity.

Explicitly **not claimed**: that a copied `.blend` can be detected by the
existing object duplicate-id repair. That mechanism repairs collisions *within
one open file* and says nothing about two files on disk. Any copy detection
adopted here must be proven by its own test before it is claimed, and the
path-mismatch rule above is the only mechanism currently proposed.

## 1.2 Control-plane metadata is not authored state

RoboVision stores its own bookkeeping on the datablocks it addresses:
`_robovision_id` on the object, `_robovision_mesh_revision` and
`_robovision_topology` on the mesh. These are control-plane metadata, not
something a user authored, and they are excluded from the authored custom
properties a snapshot reports. Without that exclusion the host's own bookkeeping
would appear as user data and minting an id would read as a scene edit.

The exclusion is of the *storage*, not the values. Identity and mesh revision
still reach the fingerprint through their canonical fields — `id`, and
`mesh.revision` — deliberately, because an object whose identity changed is not
the same object. Writing one of these properties to the value it already holds
moves nothing.
