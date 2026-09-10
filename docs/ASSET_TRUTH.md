# Deterministic Asset Truth

Status: **v0, partially implemented on the Blender host.** What is implemented is
marked; the rest is scope, not a claim.

The objective is not to complete asset validation. It is to make the first
measurable artist correction possible: a loop that observes, proposes a
candidate, observes again, and can say — with numbers rather than with confidence
— whether to keep it.

## 0. A measurement is only worth what it can be compared with

**Implemented** (`truth.certificate`).

Three things make a number evidence rather than a reading.

**Pins.** A certificate belongs to the state it was taken from: world
incarnation, coordinate contract, units, state domain, authored revision, scene
fingerprint, and each subject's mesh revision. A Q1 compared against a Q0 from a
different world, a reinterpreted unit or a different set of subjects is
**refused** — `INCOMPARABLE_MEASUREMENTS` — not warned about. The revision and
the fingerprint deliberately may differ: those are what a correction changes.

**Metrics,** each a scalar with a declared direction — `lower_better`,
`higher_better`, `neutral`. The direction travels with the metric so a caller can
say "the target must improve by epsilon" and "protected metrics may not regress"
without a lookup table that would eventually disagree with the measurement it
describes.

**Invariants,** each a boolean with its evidence. These are the hard gate: a
candidate that breaks one is rejected however much it improved what it was aiming
at. Kept apart from metrics because "worse" and "broken" are different outcomes,
and collapsing them lets a loop trade correctness for its own objective.

A certificate's id is a hash of its pins, metric values and invariant outcomes,
so "did anything change?" is a string comparison before it is a diff. Anything
that could not be established is listed in `limits` — an unmeasured property must
never be indistinguishable from a passing one. There is deliberately **no overall
quality score**; a single number is exactly what would let a loop trade an
invariant for an improvement.

## 1. Geometry truth

**Implemented** (`truth.geometry`).

Metrics: non-manifold edges and vertices, degenerate faces, zero-length edges,
loose vertices, wire edges, boundary loops, unintended boundary loops, connected
components, floating components, inconsistent-normal edges, inverted solids,
self-intersecting face pairs, face/triangle/quad counts, quad share.

Invariants: `manifold`, `no_degenerate_faces`, `no_zero_length_edges`,
`no_loose_geometry`, `normals_consistent`, `normals_outward`,
`no_unintended_open_boundaries`, `no_floating_components`,
`no_self_intersection`.

Three decisions worth stating, each measured rather than assumed:

- **Manifoldness and openness are different defects.** Blender's
  `edge.is_manifold` is also false for a boundary edge, so a single "manifold"
  check makes an intentionally open surface permanently invalid — a cloth panel
  or a cut-away section could never pass. Non-manifold here means topology no
  surface can have: an edge shared by three or more faces, or a vertex whose
  faces do not form a single fan.
- **Intentional-open semantics.** An open boundary is a fact and is always
  reported; only openness *nobody declared* is a gate. The caller names the
  subjects meant to be open. A validator that called every boundary an error
  would be switched off within a day, and a loop that trusted it would spend its
  budget welding holes that belong there. Each subject carries an explicit
  `openness` of `closed`, `intentionally_open` or `unexpectedly_open`, and a
  separate `non_manifold` flag — orthogonal on purpose, because a surface can be
  legitimately open *and* structurally broken, and an agent told only "invalid"
  cannot tell which of the two to fix.
- **Holes are counted as holes.** Boundary edges are grouped into loops, because
  an agent asked to close a hole needs the number of holes, not that there are
  sixty-eight edges involved.
- **Inconsistent winding and an inside-out solid are separate.** The first is
  read from the topology — two faces traversing a shared edge the same way —
  rather than from a dot product with a guessed viewpoint. The second is a
  negative signed volume on a closed, consistently wound mesh, and is only asked
  where that quantity exists.

Self-intersection uses BVH overlap with face pairs that share a vertex filtered
out, capped, and reported as a `limit` when truncated or not requested.

## 2. Coordinate and scale truth

**Implemented** (`truth.spatial`).

World and local bounds, dimensions, the full transform including uniformity and
mirroring, and the pivot with its offset from the bounds centre. Invariants:
`pivots_within_bounds`, `non_degenerate_extent`, and `within_declared_size` —
which is only evaluated when a `max_dimension` is declared, because a size limit
is a brief and not a property of geometry. Without one the certificate reports it
unmeasured rather than inventing a default and then gating on it.

Dimensions carry the coordinate contract they were measured in. Converting them
to another editor's frame is a **later cross-editor certificate with a proof of
its own**, not a multiply buried in a measurement: the two hosts publish
different frames on purpose.

## 3. Deterministic view coverage

**Implemented** (`truth.views`, `truth.coverage`).

**Cameras are derived from the asset, never from a viewport.** A verification
view that depended on where somebody left the view rotated would make two
measurements of the same asset incomparable, and the whole Q0/Q1 mechanic would
rest on it. Directions come from a geodesic subdivision of an icosahedron —
level 0/1/2 giving 12/42/162 — rather than a latitude/longitude grid, which piles
samples at the poles and starves the equator. Framing follows the subject's
bounding sphere, and the whole construction is identified by a `sampler` id that
changes when the level, the projection or the margin does, so two coverage
numbers from different constructions can never be compared as though they were
the same measurement. Every camera carries position, direction, up, target,
projection, clipping, image size and both matrices.

**Coverage means surface evidence, not screenshots.** The surface is sampled
area-weighted with a fixed low-discrepancy sequence, and each sample is observed
only if it is front-facing to a camera *and* an unoccluded ray reaches it. The
BVH is built from every subject together, so one part shadowing another counts as
the occlusion it is — a per-object test would report a slab's underside visible
because nothing belonging to the slab is in the way.

**It is geometric, so it needs no graphics context.** This is deliberate. The
definition of the observation does not depend on whether a display happened to be
available, which means coverage is reproducible in headless CI and on a machine
with no GPU. `limits` always carries the sampling approximation: a fraction
computed from four thousand points must not read as a continuous proof.

**The next view is calculated, not suggested.** Visibility is computed for every
candidate, so the recommendation is the argmax of newly observed area with a
predicted gain attached — and the gate proves the prediction matches the measured
gain and that no other candidate would have done better. A model handed a list of
directions and asked to choose would be guessing at something the system can
solve exactly, and its guess would not be reproducible.

Unobserved surface is reported as merged regions with area, centroid and example
faces. Reported as raw grid cells it produced 439 "regions" for a single
concealed underside — a correct partition and useless to a model deciding where
to look; merging adjacent cells gives 2.

## 4. Reference shape truth

**Implemented** (`truth.reference`).

**The silhouette is raycast through the canonical camera, not rendered.** A
silhouette is geometric occupancy, so raycasting computes it exactly — no
shading, no anti-aliasing, no render-pipeline dependence, no graphics context,
and exactly aligned to the matrices the view contract recorded. A shaded render
of the same camera remains available through the perception path for a human to
look at; it is simply not what a metric is computed from, because then the metric
would be partly about the renderer.

**Metrics are hierarchical, and there is no `reference_quality` scalar.**
`reference.macro.*` carries silhouette IoU, excess and deficit *separately*
(they are opposite corrections), area ratio, aspect error and per-axis extent
ratios. `reference.contour.*` carries symmetric Chamfer distance over contour
pixels — exact, via a Felzenszwalb distance transform, because an approximate
chamfer mask biases diagonals — plus each one-sided distance, the worst
excursion, and the worst angular sector with its sign. Two differently shaped
outlines can preserve substantial overlap, so IoU alone is not enough; contour
distance alone cannot say which direction the error is in. The namespacing leaves
room for the secondary-distribution and local-correspondence levels without
renaming anything.

**Framing must be declared, or the proportion metric lies.** Canonical cameras
normally frame on the subject's own bounds, which is right for coverage and wrong
here: a subject that grew is framed from further away, so its silhouette occupies
much the same part of the frame. Measured — a box widened by 60% reported 24%
excess. Passing the frame captured with the reference gives 61.8%. Without a
declared frame the comparison still runs and records
`reference.absolute_proportion` as unmeasured.

**Scope is stated, not implied.** A single reference constrains only the outline
from one view; `reference.constrains_hidden_geometry` says so. Nothing solves for
the reference's own camera, so `reference.camera_calibrated` is declared
unmeasured and a mismatch in the reference's viewpoint would appear as shape
error. A reference that thresholds to an empty mask is a setup error reported as
such, not a score of zero.

## 5. Pattern and repetition truth

**Implemented** (`truth.pattern`). Promoted into v0: exact repetition and spacing
are a persistent weakness of generated geometry and central to the hard-surface
class we intend to benchmark.

Declared rather than discovered. Unsupervised detection of every repeated
structure in arbitrary geometry is a much larger problem, and getting it wrong
would produce confident nonsense about geometry nobody claimed was patterned. The
caller declares a `linear`, `radial` or `mirror` pattern with a count, axis,
optional spacing and optional tolerances; members come from separate objects or
from the connected islands of one mesh, because arrays are built both ways.

**Count is the only binary.** Seven fins where eight were required is a missing
fin. Spacing, orientation and dimensional consistency are gradients with declared
tolerances, and where no tolerance was declared the property is measured and
reported as ungated rather than held to a number nobody asked for — an array that
is imperceptibly uneven is usually finished work.

Two definitions were corrected by their own gate. A radial array's wrap-around
gap is counted, or a ring missing a member looks evenly spaced with one fewer
interval. And member extent is measured in the member's **own principal frame**:
read from a world-axis-aligned box, a fin rotated six degrees reported an 8%
dimensional inconsistency while being exactly the same size as its siblings,
which made rotating and resizing — different corrections — one number.

**Known limit: orientation is measured between members, not against the pattern
frame.** `pattern.max_angular_deviation` is the largest disagreement between a
member's own principal axis and its siblings'. That is exactly right for a
repeated part and silently wrong for an array whose members turn *with* the
array: five brackets facing outward around a ring would report a 72 degree
"deviation" that is the design. The limit is disclosed rather than worked around
— `rvbench:correction-transfer/v2` authors its radial brackets co-oriented and
says so in its brief and manifest, instead of the validator being adjusted to
suit a benchmark.

The general fix, recorded here and deliberately not built yet, is an
`orientation_mode` on the declaration:

| mode | meaning |
| --- | --- |
| `co_oriented` | every member shares one orientation (today's only behaviour) |
| `radial_outward` / `radial_inward` | members face away from / toward the axis |
| `tangent_clockwise` / `tangent_counterclockwise` | members face along the ring |
| `custom_reference_axis` | the caller supplies the intended per-member frame |

That would separate three things this metric currently conflates: **placement
angle** — where around the ring a member sits; **member orientation** — which way
it faces; and **roll** — how it is turned about its own intended axis. Placement
is already measured well by radial spacing, which is why v2 remains a fair test
of the fault it actually contains.

Not now. It is a real piece of modelling work, and doing it between a benchmark
freeze and its first run would be the exact thing this project refuses: changing
what a measurement means while a comparison is in flight.

## 6. Edit locality

**Implemented** (`truth.locality`).

A correction declares its blast radius *before* it runs: targets that may change,
objects that are protected, and any dependency it is explicitly allowed to touch.
Afterwards the current state is compared against a snapshot handle from
`scene.snapshot` — the host's own deep snapshot and diff, not a second
fingerprinting scheme, because a second source of truth about what changed would
eventually disagree with the one rollbacks are verified against.

Invariants: `protected_unchanged` (a protected object that was *deleted* counts,
since the point of protecting it was that it survives), `no_undeclared_changes`,
and `no_undeclared_objects` — the last catching the classic quiet failure where
the correction worked and the file now contains a helper cube nobody meant to
ship. A declaration naming an object as both target and protected is refused
rather than resolved, and a declaration with no targets is refused because a
correction with no declared blast radius cannot be checked for staying inside it.

**Object-level, and it says so.** `locality.element_granularity` is declared
unmeasured: post-operation topology indices are not stable enough across the
operations that would need checking for a face-level claim to be honest. Mesh
revisions are reported per changed subject, which is the strongest granularity
the host can currently stand behind.

## 7. Correction evaluation contract

**Implemented** (`truth.evaluate`).

The deterministic half of the Artist Loop. Deciding *what* to try stays the
frontier model's job; deciding whether the attempt earned its commit is
arithmetic, and arithmetic is what should be trusted with a commit.

**No weighted scalar.** Acceptance is a vector of constraints that must all hold:
every required invariant still passes, every declared target improves by at least
its epsilon *in the direction its own metric declares*, no protected metric
regresses beyond its tolerance, and locality passes. A single quality number
would teach an agent that a large improvement in a cheap metric buys a small
regression in an expensive one, and eventually buys a broken mesh.

**Direction comes from the measurement,** never from a table here — a table would
drift from the metrics it describes, and it would drift in the direction of
accepting regressions. A `neutral` metric cannot be a target at all, because
nothing could decide whether it moved the right way; it can still be protected,
against movement in either direction.

**Metric names are qualified by certificate.** A front-view and a side-view
reference comparison both publish `reference.macro.excess_fraction`, and they are
exactly the pair a policy treats differently — improve the front, protect the
side. An unqualified name that matches two certificates is `indeterminate`, not
whichever was iterated first. An invariant name appearing twice holds only if
every copy holds.

**Missing evidence is never acceptance.** A required invariant that could not be
established, a metric absent from one side, an ambiguous name: `indeterminate`,
with the missing piece named. A comparison across incompatible measurements is
`reject`, because a correction whose result cannot be verified must not be kept
and the safe outcome of "I cannot tell" is to roll back. Rejection outranks
indeterminacy — a candidate that broke a gate is not merely unproven.

`begin_correction` in `system.health` refuses to call this capability available
where a verified rollback cannot be performed, because the reject branch is half
of the contract.

## Completion

DAT v0 can now answer, deterministically:

1. is this geometry structurally valid under its declared open/closed semantics?
   — `truth.geometry`
2. what physical size, frame and state does this measurement describe? —
   `truth.spatial`
3. which parts of the asset have actually been visually inspected? —
   `truth.coverage`, with the next-best camera calculated
4. how does its observed profile differ from a reference? — `truth.reference`
5. does a declared repeated structure satisfy its pattern? — `truth.pattern`
6. did a correction change only what it was allowed to change? — `truth.locality`
7. did Q1 improve the requested metric enough without violating invariants or
   protected qualities? — `truth.evaluate`
8. therefore: keep, reject, or treat as indeterminate? — the same, with causes

## Recorded but not gated

Detail spectrum, detail spatial organisation, material causality. Measured later,
and deliberately not used to accept or reject a correction until there is
evidence about what their thresholds should be.

## Deliberately absent

UVs, bakes, retopology, rigging, full part graphs, generator adapters, and a
large metric library. Building them before the first closed loop runs would be
measurement for its own sake.
