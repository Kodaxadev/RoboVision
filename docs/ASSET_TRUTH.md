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

*Not yet implemented.* A canonical deterministic camera set, per-surface
visibility coverage, identification of unverified regions, and the ability to
choose further views from what is still uncovered.

## 4. Reference shape truth

*Not yet implemented.* Silhouette masks, silhouette IoU, contour distance,
reference-view provenance, and an explicit separation between shape that a
reference view constrains and geometry it cannot see.

## 5. Edit locality

*Not yet implemented.* A declared target region and declared protected entities,
a pre/post diff, and an out-of-scope change that is measurable and can reject a
correction on its own.

## 6. Correction evaluation contract

*Not yet implemented as a driver*; the measurement half it needs exists.
Capture Q0, execute the candidate inside a transaction, capture Q1, require every
hard invariant to still hold, require the target metric to improve by epsilon,
require protected metrics not to regress beyond tolerance — then commit, or roll
back with proof.

`begin_correction` in `system.health` already refuses to call this capability
available where a verified rollback cannot be performed, because the reject
branch is half of the contract.

## Recorded but not gated

Detail spectrum, detail spatial organisation, material causality. Measured later,
and deliberately not used to accept or reject a correction until there is
evidence about what their thresholds should be.

## Deliberately absent

UVs, bakes, retopology, rigging, full part graphs, generator adapters, and a
large metric library. Building them before the first closed loop runs would be
measurement for its own sake.
