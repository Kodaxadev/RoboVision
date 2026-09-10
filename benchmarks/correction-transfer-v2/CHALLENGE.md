# Correction transfer — `rvbench:correction-transfer/v2`

You are the reasoning client for a 3D asset correction loop. A flawed asset
already exists in a live editor. Your task is to make it measurably better
without breaking what must not break.

You are **not** being asked to model anything from scratch. Whether a model can
*create* a good asset is a separate experiment. This one isolates a single
question: can you use structured measurements of a 3D asset to diagnose it,
choose a useful correction, and recognise when a correction did not work?

## What you have

- **This directory** — the brief, two reference silhouettes, and this file.
- **A benchmark facade** over a live RoboVision session. Everything you need to
  observe is available through it and costs you nothing:
  - `call(method, params)` — any read-only RoboVision operation:
    `scene.describe`, `scene.snapshot`, `scene.raycast`, `object.inspect`,
    `mesh.inspect`, `mesh.query`, `truth.*` (geometry, spatial, coverage,
    reference, pattern, views, silhouette, compare, measure, locality,
    evaluate), `viewport.focus` / `viewport.axis` / `viewport.capture` for
    shaded pictures, and `system.*`.
  - `schema(method)` — the exact parameter schema of *any* operation, including
    the mutating ones you will name in a correction. Reading a mutation's
    schema is how a correction gets written; it is not performing one.
  - `health()` — the structured readiness report.
  - `observe()` — the discrepancy packet: hard invariants, dimensions, coverage,
    per-view reference agreement, pattern conformance, and an explicit `limits`
    block listing what is *not* measured.
- **One write path**, described below.

The facade will refuse any operation the host declares mutating, and all
transaction control. That is enforced, not requested — you cannot accidentally
author anything outside an attempt, so explore as freely as you like.

## What you do not have, and why

You do not receive the RoboVision repository, the code that generated this
challenge, the seed it used, the asset's intended proportions, the recipe that
built the starting asset, or any previous participant's attempts. Not to be
adversarial: the measurements are meant to be sufficient, and if they are not,
that is the most useful result this benchmark can produce. Say so if you hit it.

## How you act

You never mutate the editor. You emit a **correction JSON** and the harness
executes it exactly as written:

```json
{
  "objective": "your own words; recorded, never parsed",
  "reason": "what in the evidence led you here",
  "targets":   [{"metric": "reference.macro.excess_fraction", "kind": "ref:front", "epsilon": 0.03}],
  "protected": [{"metric": "reference.macro.deficit_fraction", "kind": "ref:side", "tolerance": 0.02},
                {"metric": "pattern.spacing_max_error", "kind": "pattern:brackets", "tolerance": 0.25}],
  "locality":  {"targets": ["Mast"], "protected": [], "allowed": []},
  "operations": [
    {"method": "object.transform", "params": {"object": "Mast", "scale": [0.1, 0.1, 0.6]}}
  ],
  "advisory": ["coverage.observed_fraction"]
}
```

- `metric` names are certificate metric names; `kind` names which certificate it
  belongs to. The exact set is the `certificates` block of the packet —
  `geometry`, `spatial`, `coverage`, `ref:front`, `ref:side`,
  `pattern:brackets`. A metric name appearing in more than one certificate
  without a `kind` is refused as ambiguous rather than resolved by guessing.
- `targets` are the metrics you claim will improve, each by at least `epsilon`.
- `protected` are metrics you claim will not worsen by more than `tolerance`.
- `locality` is your declaration of what you intend to touch. Anything changing
  outside it fails the attempt.
- `operations` are typed RoboVision calls, executed in order inside one
  transaction, each individually pinned to the world, the coordinate contract
  and the current revision.

Submitting one correction consumes one attempt and returns the result together
with refreshed evidence.

## How you are judged

Vector acceptance, per attempt:

1. every required invariant still holds;
2. every declared target improved by at least its epsilon;
3. no protected metric regressed beyond its tolerance;
4. nothing outside your declared locality changed.

All four, or the attempt is **rejected and rolled back** — the scene returns to
its exact pre-attempt fingerprint and you try again with the evidence from the
failure. There is no partial credit and no overall quality score.

Your epsilon is audited against the metric's own measurement resolution. An
improvement smaller than the metric can resolve is recorded as **unproven**, not
as success. Declaring a tiny epsilon to guarantee a pass does not work and is
visible in the record.

## Budget

**8 attempts.** Accepted, rejected and indeterminate all count. **Observation is
unbudgeted** — looking is free, and observation calls are counted and reported
separately from correction calls rather than folded into one figure.

You may stop early. If you do, call `stop(reason, note)` with one of:
`no_worthwhile_correction_remains`, `cannot_infer_safe_correction`,
`evidence_insufficient`, `repeated_rejection`, `other`. Stopping deliberately is
a result, not a forfeit; a run that ends with an honest "the evidence does not
localise this" is more informative than one that spends its budget guessing.

## What the measurements are, honestly

Coverage and reference agreement are computed by raycast. They are **projected
geometric occupancy** and **sampled surface visibility**. They say nothing about
shading, materials, lighting, texture or cinematic presentation. The shaded
viewport captures are for your own judgement and are never fed to a metric.

The references are silhouettes of the intended asset, captured before any fault
was applied. Unlike some earlier fixtures they are *achievable*: a fully
corrected asset agrees with them almost exactly, so there is no irreducible
residual you need to reason around.

`brief.json` lists the faults that are possible and the limits that are known.
Knowing that a bracket may be misplaced is not knowing which one, in which
direction, or by how much.

## What a good run looks like

Not a threshold. The evidence being sought is whether a model unfamiliar with
this system can interpret its measurements, choose at least one useful
correction, survive a rejected correction without corrupting the asset, and
finish with measurable improvement and hard invariants intact. Say what you
believe and why at each step; the reasoning is part of the result.
