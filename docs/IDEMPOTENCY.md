# Idempotency, recipes and the operation ledger

Part of the contract in [CROSS_EDITOR_STATE.md](CROSS_EDITOR_STATE.md), kept
separately because five identifiers, a durable two-record ledger and a
determinism contract are each load-bearing on their own. Section numbers are
preserved so existing references still resolve.

## 7. Idempotency, recipes and the operation ledger

**Implemented in the Blender host; the Unity port is next.**

A lost reply must not become a second bevel. Measured on a host with no
deduplication: a resent `object.create` produced two objects, a resent
`object.delete` reported `NOT_FOUND` for an operation that had *succeeded*, and a
resent absolute transform reported `noop` — which looked like protection and was
only an operation whose second application happened not to move anything.

### 7.1 Five identifiers, five questions

Collapsing any pair of these loses something specific:

| identifier | answers |
| --- | --- |
| `request_id` | which wire request and response is this? |
| `idempotency_key` | is this another delivery of the same intended side effect? |
| `attempt` | which delivery of that logical invocation? |
| `recipe_hash` | what deterministic computation was requested? |
| seed channels | which stochastic branch of that recipe? |

A deliberate second execution of an identical recipe is possible and must stay
possible: it gets a new key. A retry of one logical invocation keeps both the key
and the recipe. The key is therefore **not** derived from the recipe — an artist
loop running one recipe in two candidate branches must get two results, not watch
the second vanish as a network duplicate.

The recipe hash covers method, canonical parameters, targets and their required
revisions, seed channels, determinism class, external model version where one
applies, and an **environment** block. A host version alone is only
implementation identity if it is guaranteed to change whenever execution
semantics change, and nothing guarantees that — so the environment records the
RoboVision build, the recipe schema, the editor and its version, the coordinate
frame and the unit convention. None of that proves identical output; only an
output hash does, and recording those is later work. It excludes transport identity —
request id, attempt, key, bridge — because those say which delivery this is, not
what was asked for. The canonical serialization is sorted-key JSON with a schema
version, defined in one place, because changing it changes every hash ever
recorded.

### 7.2 What a delivery means

Scoped to `(world, key)`. The world, because an operation was planned against
one editing context and a retry must never be reinterpreted in another; not the
bridge, which can be rebuilt while the world it was editing is verifiably still
open.

Not the transaction either. A transaction is recorded context, not a namespace:
if one key could name different side effects in different transactions, the
tombstone left after a transaction ended would be ambiguous about which
operation it described. A deliberate execution inside another transaction uses a
fresh key, exactly as a deliberate re-execution anywhere else does.

| situation | behaviour |
| --- | --- |
| first delivery, no record | reserve the key, write a durable intent, execute once |
| same key, same recipe, still reserved | `IN_PROGRESS`, no second execution |
| same key, same recipe, terminal | the original result, `replayed: true`, `original_execution`, no execution |
| same key, different recipe | `IDEMPOTENCY_MISMATCH` with both hashes, no execution |
| `attempt > 1` with no surviving record | `INDETERMINATE`, never executed |
| retry naming a replaced world | `STALE_WORLD` |
| interrupted: intent with no terminal record | `INDETERMINATE` |
| failed, recovery proved nothing applied | recorded `proved_not_applied`; the key may execute again |

Proved non-application is a **durable state, not a deletion**. Dropping the live
record made the key eligible again, which is correct, but the append-only ledger
still held the attempt: a bridge resuming from it would have seen an intent with
a result, called the operation complete, and replayed a response that was never
produced. Append-only evidence is never faked away — the record says the attempt
had no effect, and both the live host and a resumed one reach the same
conclusion.

The earlier draft of this section promised `INDETERMINATE` for a duplicate after
a bridge reload. A host cannot deliver that on its own: a forgotten key and a
never-seen key are indistinguishable, so it would execute and produce exactly the
second bevel this section exists to prevent. The client supplies the retry
evidence — an attempt counter — and the host refuses to guess. Where the record
did survive durably, the reload replays it instead.

**Transaction-scoped records are not discarded with the transaction.** The
earlier draft said they were; taken literally that reopens the hole — operation
executes, reply is lost, transaction rolls back, client retries, and with the key
gone it executes again against a scene where the first one was undone. The record
survives as a tombstone and reports what became of the transaction.

### 7.2.1 A replay is not a claim about now

The envelope around a replayed response describes the host as it is: current
revision, current world, current journal position. The response body is the
original result. Those are different things, and a client must never conclude
that an operation executed at the revision its retry happened to arrive at.

So a replay carries `original_execution`: the original request id, the world, the
recipe hash, the pre- and post-revision, the pre- and post-fingerprint, the
outcome, and what became of the transaction it ran in if there was one. "Verbatim
replay" would be the wrong promise; this is the accurate one.

### 7.3 Seeds are declared, never invented

Stochasticity is tool metadata, enforced at dispatch the way read consistency
already is. A tool names its randomness **channels** — `seed`, or
`geometry_seed`/`texture_seed`/`image_seed` for a pipeline whose branches must be
independently reproducible — and declares a determinism class: `exact`, `seeded`,
or `external_or_unproven` where a backend records its seeds and versions but does
not promise bit-identical output. The registry refuses a seeded tool with no
channels and a seeded tool claiming exactness.

A missing seed is `SEED_REQUIRED`, refused before any side effect. The host never
picks one: if it did, and the reply were lost, the retry could not describe the
computation that may already have happened. The proof of determinism is
ultimately the output hash, not the presence of a seed.

### 7.4 The operation ledger

Two append-only record types, because one post-operation entry only suffices if
crashes happen at convenient moments:

- `OP_INTENT` — durable **before** any side effect: request id, key, attempt,
  method, recipe hash, parameter hash, seeds, determinism, coordinate frame,
  transaction, state domain, pre-revision, pre-fingerprint, tool version
- `OP_RESULT` — linked to it: outcome, post-revision, post-fingerprint, journal
  cursor, the response, recovery result

An intent with no terminal record is evidence of an interrupted operation, and
is answered `INDETERMINATE` rather than re-run. One file per world incarnation,
flushed and fsynced per record, re-read when a bridge wakes up in a world it can
verify.

Deliberately thin: not replay, not a retention policy, not a query language. It
is the durable spine idempotency needs now and the artist loop's experiment trace
will need later, built once so the two cannot disagree about what happened. The
schema leaves room for `experiment_id`, `candidate_id` and `step_id`, so a future
loop can derive a stable invocation key from experiment plus candidate plus step
while the recipe hash independently identifies what that step computed.

### 7.5 Coordinate frame and units

Every recipe records the frame it executed in, and no operation may change
forward, up or handedness. Converting to another convention is a later explicit
export operation with its own recipe and its own proof. This is what makes "the
mesh came out rotated but the task reported success" a detectable defect rather
than an argument about export settings.

The canonical frame means **one Blender unit is one RoboVision metre**,
independent of what the unit display is set to. Blender's `scale_length` is what
makes that a claim rather than a tautology: it says how many metres one unit
represents, it is a scene property a user can change, and at 0.01 geometry that
measures two units is two centimetres. An export resolving units differently from
the host would rescale the asset while every operation reported success. So every
recipe records `unit_scale_length`, the unit system, and whether the scale is
canonical — reported rather than assumed, and available to the coordinate
certificate that will later prove it end to end.

### 7.6 Mutating is not the same claim as side-effecting

`mutating` means an operation changes authored scene state. `side_effecting`
means repeated delivery is not free. Every mutating tool is side-effecting;
transaction control is side-effecting without being mutating, and so are the
artifact export and external generation operations that will arrive later. The
distinction exists so that "does not advance the scene revision" can never be
mistaken for "safe to execute twice".

Transaction control is deliberately not covered by idempotent replay yet. Each
verb is already duplicate-safe through its own state machine — a second begin is
`TRANSACTION_ACTIVE`, a second commit or rollback is `TRANSACTION_FINISHED`, a
second adopt is refused because the transaction is no longer orphaned — and
`transaction.begin`'s response carries the recovery token, which must not be
written to a ledger or handed back by a replay to whoever redelivers the request.
Replaying these needs deliberate redaction, designed when something needs it.

### 7.7 The autonomous contract

Low-level delivery stays permissive, so an operator at a console can still poke
the host. An agent driving it unattended cannot be trusted to have remembered
any of this by convention, so a request may declare `contract: "autonomous"`, and
an authored mutation under it must carry `expected_world`, `if_revision`,
`idempotency_key`, a valid `attempt`, and every seed channel the tool declares.
Anything missing is `CONTRACT_VIOLATION`, refused before any side effect.

The world requirement is the one that is easy to underestimate: a **first**
delivery planned against world A must not execute in world B merely because it is
technically not a retry, and nothing but an explicit expected world catches that.

### 7.8 Storage tiers, one schema

The ledger is written under a temporary root today, which is adequate for what it
currently is: a crash and reload write-ahead log for one editor session. The
Artist Loop's experiment history needs the same records under a workspace or
project artifact root, with its own retention — a different storage tier for one
schema, not a second audit system that would eventually disagree with this one.
