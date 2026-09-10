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

## Epsilon is audited, not chosen

Every attempt records, per target: the epsilon, the metric's own resolution, the
achieved improvement, and whether each cleared the other. An improvement below
the measurement's quantisation is unproven, not success. The frontier run's final
attempt was rejected on exactly that basis — 0.00105 against a resolution of
0.00276.

## The blind correction-transfer benchmark

`benchmarks/correction-transfer-v1/` is the frozen package a participating model
receives, and it is a directory rather than a repository checkout because most of
what must be withheld is withheld by not being in it. It contains the brief, the
two reference silhouettes, the frozen A0 as a replayable operation list, a
manifest with SHA-256 digests of every file, and `CHALLENGE.md` — the exact
interface the model is given.

A0 is **replayed, not rebuilt**: every participant starts from a byte-identical
asset, which is the premise of a transfer experiment and is not achieved by
constructing something the same way twice.

**Correction transfer is separated from creation.** The first experiment asks
only: can model M use RoboVision evidence to diagnose and improve an existing 3D
asset? All models start from the same A0. Whether a model can construct A0 from
the brief and then improve its own creation is a second experiment, run only
afterwards, so that a poor generator result is never read as an inability to use
the correction loop.

Withheld from participants: the blockout generator, the proxy seed, fixture
implementation, any dimension not in the brief, the repository source, and every
previous model's trajectory. Disclosed: the brief, the references, the live
asset, the public tool surface and its schemas, `system.health`, authoritative
observations, the discrepancy packet, and the shaded viewport captures.

One limit is disclosed rather than withheld: the references depict a coarse
blockout with no cooling-stack detail, so reference metrics cannot reach zero on
a correct asset. Hiding that would not test a skill — it would make every model
chase a target that does not exist and report a measurement artefact as a
difference between models.

Frozen before the first independent run: 8 attempts (accepted, rejected and
indeterminate all count; observation is free), identical tools, identical DAT
policy, identical references. Epsilon and tolerances are never tuned per model.
If a real DAT metric defect is found mid-comparison the comparison stops, the
defect is fixed with an independent regression test, the benchmark is versioned
and affected runs restart — the metrics are not adjusted because a model
dislikes them.

`tests/artist_loop/benchmark.py` runs it: `restore`, `observe`, `attempt`,
`finalize`. The budget comes from the manifest, never from the command line.
`finalize` records model identity, accepted/rejected/indeterminate counts,
per-attempt rollback proofs, tool calls, elapsed time, Q0 and final DAT vectors,
hard invariants, captures, manual interventions and stop reason. Token usage is
recorded as self-reported or null: it is not observable from inside the harness
and an estimate would be the easiest number in the comparison to be wrong about.
No scalar quality score is produced.

**The Opus run is the baseline, and it is not independent.** It built A0, it
wrote DAT and it wrote this loop. It is kept as the builder/client baseline and
labelled as such. The success criterion for the first independent model is not a
threshold: it is evidence that a model unfamiliar with RoboVision can interpret
its measurements, choose at least one useful correction, survive a rejected
correction without corrupting the asset, and finish with measurable improvement
and hard invariants intact — preferably with a before/after a human judges better.

## What is deliberately absent

No generator integrations, no provider routing, no skill DAG, no skill extraction,
no part graph, no UV or baking, no retopology, no material scoring, no detail
gating, no Unity authoring, no autonomous planner. The loop exists to find out
whether the correction mechanism itself is worth anything before any of that.
