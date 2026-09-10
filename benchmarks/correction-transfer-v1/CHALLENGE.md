> **Status: harness validation. Not a blind benchmark.**
> The material that generated this challenge — the blockout generator, the
> seed, the fixture code — is committed in the public RoboVision repository,
> so a model with web or GitHub access can retrieve it. Not handing a
> participant a checkout is not secrecy. v1 is kept because it proves the
> harness works; `correction-transfer-v2` is the blind benchmark.

# Correction transfer — `rvbench:correction-transfer/v1`

You are the reasoning client for a 3D asset correction loop. A flawed asset
already exists in a live editor. Your task is to make it measurably better
without breaking what must not break.

You are **not** being asked to model anything from scratch. Whether a model can
*create* a good asset is a separate experiment; this one isolates a single
question: can you use structured measurements of a 3D asset to diagnose it,
choose a useful correction, and recognise when a correction did not work?

## What you have

- **This directory.** The brief, two reference silhouettes, and this file.
- **A live RoboVision session** on the public tool surface. Discover it with
  `system.capabilities` and read exact parameter schemas with `system.method`.
  You may inspect the asset as freely as you like: `scene.describe`,
  `scene.snapshot`, `truth.geometry`, `truth.spatial`, `truth.coverage`,
  `truth.reference`, `truth.pattern`, `truth.views`, `truth.silhouette`, and the
  shaded viewport captures (`viewport.focus`, `viewport.axis`,
  `viewport.capture`). Observation is unbudgeted and costs you nothing.
- **`system.health`** — a structured readiness report. Read it before assuming
  you can mutate anything.
- **A discrepancy packet** printed by the harness: hard invariants, dimensions,
  coverage, per-view reference agreement, pattern conformance, and an explicit
  `limits` block listing what is *not* measured.

## What you do not have, and why

You do not receive the RoboVision repository, the code that generated the
reference images, any seed, any dimension that is not written in the brief, or
any previous participant's attempts. Not to be adversarial — the measurements
are meant to be sufficient, and if they are not, that is the most useful result
this benchmark can produce.

## How you act

You never call the editor to mutate it. You emit a **correction JSON** and the
harness executes it exactly as written:

```json
{
  "objective": "your own words; recorded, never parsed",
  "reason": "what in the evidence led you here",
  "targets":   [{"metric": "reference.macro.excess_fraction", "kind": "ref:front", "epsilon": 0.03}],
  "protected": [{"metric": "reference.macro.deficit_fraction", "kind": "ref:side", "tolerance": 0.02},
                {"metric": "pattern.spacing_max_error", "kind": "pattern:fins", "tolerance": 0.0005}],
  "locality":  {"targets": ["Body"], "protected": [], "allowed": []},
  "operations": [
    {"method": "object.transform", "params": {"object": "Body", "scale": [0.38, 0.30, 0.36]}}
  ],
  "advisory": ["coverage.observed_fraction"]
}
```

- `metric` names are the certificate metric names; `kind` names which certificate
  it belongs to, and the exact set is the `certificates` block of the packet
  (`geometry`, `spatial`, `coverage`, `ref:front`, `ref:side`, `pattern:fins`).
  A metric name that appears in more than one certificate without a `kind` is
  refused as ambiguous rather than resolved by guessing.
- `targets` are the metrics you claim will improve, each by at least `epsilon`.
- `protected` are metrics you claim will not get worse by more than `tolerance`.
- `locality` is your declaration of what you intend to touch. Changing anything
  outside it fails the attempt.
- `operations` are typed RoboVision calls, executed in order under one
  transaction, each pinned to the world, the coordinate contract and the current
  revision.

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
improvement smaller than what the metric can resolve is recorded as **unproven**,
not as success. Declaring a tiny epsilon to guarantee a pass does not work and is
visible in the record.

## Budget

**8 attempts.** Accepted, rejected and indeterminate all count. Observation does
not. You may stop early; say so and why.

## What the measurements are, honestly

Coverage and reference agreement are computed by raycast. They are **projected
geometric occupancy** and **sampled surface visibility**. They say nothing about
shading, materials, lighting, texture or cinematic presentation. The shaded
viewport captures are for your own judgement and are never fed to a metric.

The references are silhouettes of a coarse blockout massing. They constrain the
outer outline and contain no fine detail, so reference metrics measure agreement
with the intended massing — they do not reach zero on a correct asset, and
chasing the last few percent of front excess is chasing the reference's own
coarseness. `brief.json` lists the known limits in full.

## What a good run looks like

Not a threshold. The evidence being sought is whether a model unfamiliar with
this system can interpret its measurements, choose at least one useful
correction, survive a rejected correction without corrupting the asset, and
finish with measurable improvement and hard invariants intact. Say what you
believe and why at each step; the reasoning is part of the result.
