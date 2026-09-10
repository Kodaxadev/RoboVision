# Artist Loop v0

Status: **v0 complete on the Blender host, model-agnostic by construction.**

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
executes the operations it was handed, measures again, calls the evaluator, and
commits or rolls back. It does not choose what to correct. A deterministic
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

## Epsilon is audited, not chosen

Every attempt records, per target: the epsilon, the metric's own resolution, the
achieved improvement, and whether each cleared the other. An improvement below
the measurement's quantisation is unproven, not success. The frontier run's final
attempt was rejected on exactly that basis — 0.00105 against a resolution of
0.00276.

## What is deliberately absent

No generator integrations, no provider routing, no skill DAG, no skill extraction,
no part graph, no UV or baking, no retopology, no material scoring, no detail
gating, no Unity authoring, no autonomous planner. The loop exists to find out
whether the correction mechanism itself is worth anything before any of that.
