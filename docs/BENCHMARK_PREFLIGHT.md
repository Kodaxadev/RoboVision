# Runs against `rvbench:correction-transfer/v2`

Two contaminated preflights, then Participant 1 — the first clean run.

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

## Participant 1 — MiMo V2.5 Free, clean and isolated

Run identity `mimo-v2.5-clean-01`. Launched from `D:\RVBench-MiMo-01`, holding
only the five public files and a locked OpenCode config; RoboVision reached only
through the six `rvbench_*` stdio tools. No repository access, no web retrieval,
no prior trajectories, no part of any earlier conversation.

**6 attempts / 1 accepted / 1 rejected / 4 indeterminate / 5 verified rollbacks /
stopped `no_worthwhile_correction_remains`.** Restore verified against the frozen
signature and Q0. 127 correction-execution calls, 99 observation calls, 226
total, 17.95s. No manual interventions.

| | Q0 | final |
| --- | --- | --- |
| hard invariants | pass | pass |
| `pattern[brackets].max_spacing_error` | 4.900687° | **0.000103°** |
| `pattern[brackets].failing` | 2 failing | **none** |
| `reference[side].silhouette_iou` | 0.757604 | 0.759896 |
| `reference[front].silhouette_iou` | 0.862899 | 0.862899 |
| `coverage.observed_fraction` | 0.915716 | 0.915716 |

Its stop note is preserved verbatim in `stop.json`, including the belief that the
reference metrics "have null measurement resolution in this system — they cannot
be declared as correction targets". **That belief is false**, and it is kept
exactly as written. A benchmark that edited a participant's reasoning after
discovering it was mistaken would be destroying its most useful evidence.

### Why the belief was false, and what actually happened

Reference metrics have finite, declared resolution. For 256×256 masks:

```
ref:front/reference.macro.excess_fraction   = 1.52587890625e-05   (exactly 1/65536)
ref:front/reference.macro.silhouette_iou    = 1.52587890625e-05
ref:front/reference.contour.mean_distance   = 0.002762135864
```

The participant targeted `reference.excess_fraction` and
`reference.deficit_fraction`. The certificate metrics are
`reference.**macro**.excess_fraction` and `reference.**macro**.deficit_fraction`.
The `.macro.` segment was missing, so `resolutions()` could not find the key and
reported `metric_resolution: null`, and `truth.evaluate` could not find the
metric and returned `target_metric_missing` with `before: false, after: false`.

The participant read the null resolution as a property of the metric rather than
as a consequence of its own identifier. This is **cause (1): wrong metric
names** — not a misunderstood audit, and not a serialization or interface bug.

The evaluator did say so. `target_metric_missing` with both `before` and `after`
false is exactly "no metric of that name exists in either measurement". The
information was available and was outweighed by the null.

### A second, separate failure worth more attention

**Attempt 1 contained the correct fix and was blocked on contract legibility, not
on 3D reasoning.** Its target was right (`pattern.spacing_max_error`, kind
`pattern:brackets`, improvement 4.9006 against epsilon 0.5) but it also declared
the brief's four `geometry.*` **required invariants** as *protected metrics*.
Invariants are not metrics, so every one returned `protected_metric_missing` and
the attempt went indeterminate and was rolled back.

The participant then re-derived the same correction and resubmitted it without
the protected block as attempt 3, where it was accepted. So the first clean
participant found the real fault on its first try, spent two attempts on a
namespace confusion, and still got there.

### Findings recorded; v2 is not changed

The comparison is in flight and the freeze holds. All three are candidates for
v3, and all three are RoboVision-side, not benchmark-side:

1. **`target_metric_missing` and `protected_metric_missing` carry no remedy.**
   The `_ambiguous` branches beside them do — they tell the caller to name the
   kind. The missing branches report the absence and stop, naming neither the
   available metrics of that certificate nor the near-match. Adding the
   available names would have cost this participant nothing and saved it three
   attempts.
2. **The discrepancy packet publishes short names that are not the evaluator's
   names.** The packet shows `reference.front.excess_fraction` as
   `excess_fraction`, while the evaluator wants
   `reference.macro.excess_fraction`; it shows `max_spacing_error`, while the
   evaluator wants `pattern.spacing_max_error` — the words in the other order.
   `CHALLENGE.md` calls the packet the primary evidence, and prefixing a packet
   key with its section name is exactly what the packet's shape invites. The
   `certificates` block names the kinds but not the metrics inside them.
3. **The facade counts observation calls but does not record which methods were
   called.** 99 calls are known; what was inspected is not, so it cannot be
   established how the participant learned the correct *pattern* metric name
   while missing the reference one. That is a telemetry gap in post-hoc
   diagnosis, and it is the reason this section can say what the participant
   got wrong but not how it got the other one right.

### The epsilon audit does not gate acceptance

Confirmed in the frozen code. `truth.evaluate` compares improvement against the
declared epsilon and decides; `_epsilon_audit()` runs afterwards, in the record,
after commit or rollback has already happened. It is evidence, not a gate.

`CHALLENGE.md` says an improvement below resolution "is recorded as **unproven**,
not as success". The first half describes the implementation exactly; the second
half reads as though acceptance were withheld, and it is not. The wording is
ambiguous and should be tightened — or the rule made real — in v3, not here.

It changed nothing in this run, and would not have. The accepted attempt improved
`pattern.spacing_max_error` by 4.900585 against a resolution of 1e-06, roughly
five million times the noise floor, and the one rejection was rejected for
achieving 0.0 against a required 0.001. No attempt's outcome differs under the
stricter rule.

### Classification

> **Clean transfer demonstrated, partial success.** One independently diagnosed
> 3D correction accepted and preserved. Subsequent reasoning was constrained by
> an incorrect interpretation of the reference-metric contract, itself caused by
> a wrong metric identifier that the system reported but did not explain.
> Safety and reversibility behaved correctly throughout.

## What this means for the independent run

*Written before Participant 1 ran, and left as written.*

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


## Three runs, one pattern

| | access | attempts | accepted | faults fixed |
| --- | --- | --- | --- | --- |
| Preflight A — Muse | source, offline analytical | not budget-comparable | 4 predicted | 4 |
| Preflight B — Spark | source, live harness | 4 | 1 | 1 |
| Participant 1 — MiMo | **clean, isolated** | 6 | 1 | 1 |

All three independently localised the `Bracket_1` angular fault first. That is
now three reasoning clients, one of them with no access to anything, agreeing
that the radial-pattern discrepancy is legible from the evidence alone.

The divergence after that is the more useful half. Unlimited offline evaluation
solved everything; both live runs solved one. The clean run's remaining barrier
was **not** 3D reasoning — it had diagnosed the counterweight displacement and
the `Bracket_0` radius correctly, and said so in its stop note. It could not
address them because it could not name the metrics.

So the provisional bottleneck is **agent-legibility of the evidence contract**,
not spatial reasoning. That is a considerably more actionable finding than a
model that simply could not see the faults, and it is exactly the class of thing
an independent-model test existed to uncover.
