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

The recipe hash covers method, tool version, canonical parameters, targets and
their required revisions, seed channels, coordinate frame, determinism class and
external model version where one applies. It excludes transport identity —
request id, attempt, key, bridge — because those say which delivery this is, not
what was asked for. The canonical serialization is sorted-key JSON with a schema
version, defined in one place, because changing it changes every hash ever
recorded.

### 7.2 What a delivery means

Scoped to `(world, transaction-or-none, key)`. The world, because an operation
was planned against one editing context and a retry must never be reinterpreted
in another; not the bridge, which can be rebuilt while the world it was editing
is verifiably still open.

| situation | behaviour |
| --- | --- |
| first delivery, no record | reserve the key, write a durable intent, execute once |
| same key, same recipe, still reserved | `IN_PROGRESS`, no second execution |
| same key, same recipe, terminal | the original response, `replayed: true`, no execution |
| same key, different recipe | `IDEMPOTENCY_MISMATCH` with both hashes, no execution |
| `attempt > 1` with no surviving record | `INDETERMINATE`, never executed |
| retry naming a replaced world | `STALE_WORLD` |
| interrupted: intent with no terminal record | `INDETERMINATE` |
| failed, recovery proved nothing applied | the record is dropped; the key may execute again |

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

### 7.5 Coordinate frame

Every recipe records the frame it executed in, and no operation may change
forward, up or handedness. Converting to another convention is a later explicit
export operation with its own recipe and its own proof. This is what makes "the
mesh came out rotated but the task reported success" a detectable defect rather
than an argument about export settings.
