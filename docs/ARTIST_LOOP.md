# Artist Loop

Status: **v0.1 on the Blender host, model-agnostic by construction.**
v0 is the first-run evidence at `1575161` and stays exactly as it was recorded.

The thesis is not about any one model. It is whether a capable frontier model can
use a common observable, measurable, reversible environment to make a 3D asset
materially better through iterative correction — and every part of this is built
so a different model could be dropped in without RoboVision changing.

## What is model-agnostic, and how that is enforced

A correction is a JSON object: an objective, the metrics it targets, the metrics
it protects, the locality it declares, and a list of typed operations. Nothing
else passes between the reasoning client and the driver. No prose reaches
RoboVision, no RoboVision component contains model-specific language, and the
evidence a client reads is the same shape whoever consumes it.

The driver contributes no judgement. It measures, opens a pinned transaction,
delivers the operations it was handed under the strict autonomous contract,
measures again, calls the evaluator, and commits or rolls back. It does not
choose what to correct. A deterministic
priority ranking *is* computed and recorded on every attempt — failed invariant,
requirement violation, pattern failure, largest reference discrepancy, largest
unverified region — but it is never consulted. It exists so a later analysis can
ask whether a model chose better than a trivial ordering would have.

## The loop

`system.health` → authoritative observation → DAT Q0 bundle → discrepancy packet
→ *model chooses one objective and emits a correction* → pinned autonomous
transaction begin → typed operations → DAT Q1 → evaluator → commit or verified
rollback → record → re-observe.

The host runs as it would in deployment: an interactive Blender with the socket
bridge, driven from separate processes over `HostSession`. Interactive not for
pixels but because background Blender's `ed.undo` restores nothing, so a rejected
correction could not be rolled back and the reject path would be theatre.

## Evidence honesty

Coverage and reference agreement are **projected geometric occupancy** and
**sampled surface visibility**, computed by raycast. The packet labels them so no
reader can mistake a silhouette match for a rendered likeness. They say nothing
about shading, materials, lighting or texture. The only shaded evidence in the
whole loop is `capture_views`, which produces Blender viewport axis captures for a
human to look at, records that it is not the canonical measurement camera, and is
never fed to a metric.

## Strict delivery (v0.1)

Every model-requested mutation is delivered individually under the autonomous
contract: `expected_world`, `expected_coordinate_contract`, the current
`if_revision` read back from the previous response envelope, a stable
`idempotency_key` persisted to disk *before* the request is sent, and an
`attempt`. A retry of one logical operation reuses the same key and the same
recipe with a higher attempt, so a lost acknowledgement is replayed rather than
reapplied.

Mesh revision pins are deliberately not derived. A topology-indexed operation
that needs `expected_mesh_revision` states it in the model's own typed
parameters, from the model's own observation; a driver that supplied one would be
manufacturing the agreement the pin exists to prove.

**What v0 actually did.** v0 opened a fully pinned autonomous transaction and
then executed the individual correction operations through plain low-level calls.
The pins were therefore checked once, at begin. The per-operation strict envelope
was added in v0.1. The v0 trajectories are not rewritten to suggest otherwise —
they are the historical record of a mechanism experiment, and a benchmark that
edits its own evidence is not one. `tests/blender/artist_loop_delivery.py` pins
the v0.1 behaviour, including that a stale world, revision or coordinate contract
refuses *before* the next mutation lands.

## Failing closed (v0.1)

Once a transaction has begun, an exception anywhere in the driver's own
orchestration — Q1, locality, the evaluator, the closing snapshot — runs verified
rollback rather than returning as though the attempt ended. Where the connection
state is ambiguous, the existing session facilities settle it: reconnect,
`recoverable_transaction()`, adopt with the credential this session already
holds, then roll back. Where even that is impossible the terminal state is
*read*, and an unprovable outcome is reported as `unresolved` rather than assumed
safe. An incomplete evaluation never commits: "I could not finish judging this
candidate" is not evidence that the candidate was good. The failure is recorded
as a trajectory entry with decision `indeterminate`, and the exception is
re-raised.

## The delivery ledger, and what it is not

`delivery-ledger.jsonl` records each operation's idempotency key, recipe and pins
before the request that carries them, and fsyncs each entry. It **is** a pre-send
operation identity record, so a redelivery inside the live process presents the
same key and is recognised rather than repeated, and it **is** evidence for the
trajectory and for audit.

It is **not** client-process crash recovery, and no such claim is made. Nothing
reads the file back to resume an interrupted attempt, and the transaction
recovery credential that would be needed to do so lives in the `HostSession` and
dies with the process. Building process-crash resumption would widen the
architecture for no benefit this benchmark has. An attempt whose client dies is a
lost attempt; the host's own orphan handling is what protects the *scene*, which
is a different guarantee.

## The terminal boundary

The transaction's fate divides an attempt in two, and the two halves permit
completely different statements.

**Before commit or rollback returns**, an exception means the candidate was never
judged: fail closed, roll back, record `indeterminate`.

**After the host has confirmed a terminal state**, the candidate's outcome is a
fact. If evidence collection then fails — the closing snapshot, the trajectory
write — that is a failure to record what happened, not a change in what happened.
Relabelling a committed mutation as "indeterminate, rolled back" would be the
most dangerous lie this harness could tell: the scene would carry the change
while the record denied it. So the terminal outcome is preserved, the host's own
terminal state is read back, `final_fingerprint` and `restored` are left null
rather than invented, and the attempt is marked
`benchmark_validity: incomplete_evidence` — unusable as evidence, which is not
the same as undone.

## Epsilon is audited, not chosen

Every attempt records, per target: the epsilon, the metric's own resolution, the
achieved improvement, and whether each cleared the other. An improvement below
the measurement's quantisation is unproven, not success. The frontier run's final
attempt was rejected on exactly that basis — 0.00105 against a resolution of
0.00276.

## The two recovery windows

Both are proved end to end over a real socket, with the response dropped by the
transport's own fault seam, in `tests/blender/artist_loop_ack_loss.py`
(interactive Blender).

**A lost mutation acknowledgement.** The mutation reaches the host and applies;
the reply is lost; the socket dies. The driver stops delivering, the reads that
follow reconnect, and the host has meanwhile orphaned the transaction the dead
connection owned. The rollback then answers `TRANSACTION_ORPHANED` — a definite
answer, not a transport loss — and the first version of `fail_closed` resolved
definite answers by *reading*, so an active orphan holding the model's mutation
was reported `unresolved`. It is now adopted and rolled back, but only when all
three conditions hold: it is the same transaction, it requires adoption, and this
session proves it holds the credential. `TRANSACTION_FINISHED`, recovery
uncertainty, contamination and foreign ownership keep their own meanings and are
never adopted.

**A lost final snapshot after a commit.** Reported committed and incomplete.

The delivery gate's redelivery case proves duplicate *replay* and is named that
way: its first delivery succeeded and the client knew it did, which is not the
lost-acknowledgement window at all.

## The blind correction-transfer benchmark

**v1 (`benchmarks/correction-transfer-v1/`) is harness validation, not a blind
benchmark, and is labelled so in its own manifest and challenge document.** Its
generator, seed and fixture code are committed in this public repository, so a
model with web or GitHub access can retrieve them. Not handing a participant a
checkout is not secrecy. v1 is kept unaltered because it is the evidence that the
harness, the strict delivery path, the evaluator and the rollback proofs work.

**v2 (`benchmarks/correction-transfer-v2/`) is the blind benchmark.** A new
asset — an orbital relay mast with a *radial* five-bracket array, deliberately
sharing no structure with v1's linear-finned pump, so knowledge of the v1
correction strategy reveals nothing. Its references are captured from the
correct asset before any fault, so unlike v1 they are achievable and there is no
irreducible residual to reason around.

The generator, the seed, the sealed target specification and the private restore
recipe live **outside this repository**, at `$RVBENCH_PRIVATE`. Published in the
manifest before the first run are their SHA-256 commitments, so the files can be
revealed afterwards and re-hashed to show nothing was altered between
participants. A hash commitment is evidence of immutability, not disclosure.

The participant package contains only the challenge, the brief, the two
references and the manifest. It carries no construction recipe: A0 arrives
already restored in the editor. Where a construction recipe *is* in a package —
v1's `a0.json` — the manifest says `a0_construction_disclosed: true`, because a
file the participant holds must never be described as withheld.

### Normalized-authoring equivalence

"Byte-identical A0" was wrong and is retired. Blender issues fresh RoboVision
identities on every create, so two correct restores differ in every object UUID,
world incarnation, revision and journal position. What the benchmark needs is
identical *authored* geometry, and `benchmarks/signature.py` hashes exactly that:
object names, parent names by name, world transforms, local and world extents,
mesh topology counts, materials and modifiers — excluding every run-specific
identity. Every restore is checked against the frozen signature **and** against
the frozen Q0 vector, and the run refuses to begin if either differs. Both are
needed: the signature says the authored state matches, Q0 says the measurements
of it do, and they can disagree.

### The participant boundary is enforced

`benchmarks/facade.py` is the participant's whole surface. A method is permitted
only if it is on the read allowlist *and* the host itself declares it
non-mutating — the first half denies operations added to RoboVision tomorrow, the
second denies an operation that becomes mutating without anyone editing the list.
Transaction control is refused outright, `system.method` stays available for
every operation including mutating ones (reading a mutation's schema is how a
correction gets written), and there is exactly one write path,
`submit_correction`, which delegates to the existing Artist Loop harness rather
than reimplementing any of it. This is benchmark isolation, not a security
architecture.

### Budgets, counts and stopping

8 attempts; accepted, rejected and indeterminate all count. Observation is
unbudgeted, and that choice is recorded in the manifest. Calls are reported as
`correction_execution_tool_calls`, `participant_observation_tool_calls` and
`total_participant_facing_calls` — never one merged figure, which would describe
a model as more efficient than it was. Token usage is self-reported or null and
is never invented.

A run can end deliberately: `stop(reason, note)` with one of `budget_exhausted`,
`no_worthwhile_correction_remains`, `cannot_infer_safe_correction`,
`evidence_insufficient`, `repeated_rejection`, `other`. The categories make runs
comparable; the note makes one run understandable.

Epsilon and tolerances are never tuned per model. If a real DAT metric defect is
found mid-comparison the comparison stops, the defect is fixed with an
independent regression test, the benchmark is versioned and affected runs
restart. No scalar quality score is produced.

### Independence

Every model that built RoboVision, or has read hidden benchmark material, or
carries prior RoboVision conversation context, is **non-independent** and
recorded as such — the Opus v1 run included. The first genuine participant is a
fresh session, preferably from a different model family, with no repository
access and no web retrieval of it, holding only the v2 package and the facade.
It may know Blender: this is not a test of ignorance, it is a test of whether
RoboVision's exposed evidence and tools are sufficient.

## What is deliberately absent

No generator integrations, no provider routing, no skill DAG, no skill extraction,
no part graph, no UV or baking, no retopology, no material scoring, no detail
gating, no Unity authoring, no autonomous planner. The loop exists to find out
whether the correction mechanism itself is worth anything before any of that.
