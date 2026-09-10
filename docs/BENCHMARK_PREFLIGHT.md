# Preflight runs against `rvbench:correction-transfer/v2`

Two runs happened before the first genuinely independent participant. **Neither
is the independent result**, and neither is reported as one. Both clients had
access to the RoboVision source, which is the whole reason v2's generator, seed
and restore recipe live outside this repository — source access is not access to
the hidden material, but it is enough to disqualify a run from being called
blind.

They are archived because they answer two different preflight questions that the
independent run should not have to answer for the first time.

They are **not ranked against each other.** Their experimental setups differ so
much that a comparison would be meaningless: one was allowed unlimited offline
evaluation, the other spent a real attempt budget against the live harness.

## Preflight A — source-assisted offline analytical solve

Client: Muse. Not attempt-budget comparable.

Worked largely outside the facade, reconstructing and evaluating candidates
locally rather than spending attempts. Reported four proposed corrections, all
predicted to be accepted in dry-run.

What it establishes: **v2 contains enough information to infer the intended
corrections.** That is a solvability sanity check on the challenge, and a useful
one — a benchmark nobody can solve tells you nothing about the models.

Recorded from the operator's report. No trajectory artifacts from this run exist
in this repository, and its figures are not independently verified here.

## Preflight B — source-assisted live harness run

Client: Codex 5.3 Spark. Recorded by the harness under the run identity
`test-model`; the trajectory was not renamed afterwards, because a record that
says what it actually captured is worth more than a tidier label.

**4 attempts / 1 accepted / 3 indeterminate / 2 verified rollbacks / deliberate
safe stop.** Restore verified against the frozen signature and Q0. 72 correction
execution calls, 48 participant observation calls, 120 total. No manual
interventions. Token usage not observable.

| | Q0 | final |
| --- | --- | --- |
| hard invariants | pass | pass |
| `pattern[brackets].max_spacing_error` | 4.900687° | **0.000073°** |
| `pattern[brackets].failing` | 2 failing | **none** |
| `reference[side].silhouette_iou` | 0.757604 | 0.759896 |
| `reference[side].excess_fraction` | 0.182009 | 0.180470 |
| `reference[side].deficit_fraction` | 0.104505 | 0.102966 |
| `reference[front].silhouette_iou` | 0.862899 | 0.862899 |
| `coverage.observed_fraction` | 0.915716 | 0.915716 |
| dimensions | unchanged | unchanged |

Stop reason: `cannot_infer_safe_correction` — *"Pattern defects are fixed;
remaining side/front silhouette mismatch did not improve under two additional
counterweight/bracket-radius localization attempts, so further correction is not
safely inferable from current evidence."*

The run was **finalized exactly where it stopped**. It was not given a further
attempt and was not restarted. Preflight A's analysis had by then revealed which
faults remained, so extending Preflight B would have been operator leakage
dressed up as a better score.

### What Preflight B actually demonstrates

Not that a model guesses correctly. That when it guesses **incorrectly, the asset
does not degrade**:

```
good hypothesis      → proven improvement → KEEP
bad hypothesis       → unproven          → RESTORE
bad hypothesis       → unproven          → RESTORE
uncertainty          → STOP
```

Every rejected attempt returned the scene to the exact fingerprint it began with
(`128bcffc42a1`, three times over), hard invariants held throughout, and the one
proven correction stayed.

The side-view improvement is worth noting on its own. The only retained edit was
the bracket correction, and side IoU moved 0.757604 → 0.759896 as a **secondary
consequence** of it — while front IoU stayed at exactly 0.862899. The
participant did not have to claim the silhouette change as its reason for the
edit, and the evidence kept its separability instead of every number improving
together. That is the multi-metric behaviour DAT exists to expose.

### A real participant error, handled by the fail-closed path

Attempt 2 submitted a correction with `targets: []` and one `object.transform`
already delivered inside the open transaction. The evaluator refused —
*"targets is required: a correction with nothing it was trying to improve cannot
be judged to have succeeded"* — and because the refusal arrived **after** the
mutation and **before** any terminal state, the fail-closed path rolled it back
and recorded the attempt `pre_terminal`, `benchmark_validity: invalid`.

This is the first time that machinery fired on something other than an injected
fault. It is recorded, and **v2 is not changed because of it.** The participant
was told what a correction requires and produced an invalid one; that is
participant behaviour, and softening the benchmark mid-comparison is exactly what
the freeze exists to prevent.

Recorded as a future *product* ergonomic, not a benchmark change: pre-validate a
correction's shape before opening a transaction, so a malformed candidate is
returned for repair without consuming an expensive execution.

## What this means for the independent run

The bar for the first clean participant is **not** Preflight A's four-for-four.
A result shaped like Preflight B — from a model that has never seen this
repository — would already establish the thing being tested:

> An unfamiliar frontier model used RoboVision evidence to discover a real 3D
> fault, made a quantitatively successful correction, had incorrect hypotheses
> safely rejected and restored, and knew when to stop rather than corrupting the
> asset.

Solving all four faults would be substantially stronger. Neither preflight
replaces it.

One thing both preflights agree on, independently: the **Bracket_1 angular fault
was found first, by both clients.** That is real evidence the radial-pattern
discrepancy is legible across reasoning clients rather than an artefact of one.
After that they diverged completely, which is the more interesting half — a
model with source access does not automatically extract everything the
measurements contain, so v2 still carries a genuine reasoning burden.
