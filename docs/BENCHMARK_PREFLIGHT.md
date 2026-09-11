# Runs against `rvbench:correction-transfer/v2`

Two contaminated preflights, then two clean participants.

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

~~The evaluator did say so … The information was available and was outweighed
by the null.~~ **Retracted — that was wrong.** The evaluator did compute
`target_metric_missing`, but only into the trajectory on disk. `submit_correction`
returned the decision, `targets_achieved` and the epsilon audit, and **never
`reject_causes` or `indeterminate_causes`**. What this participant actually saw
was `decision: indeterminate`, an empty `targets_achieved`, and an audit reading
`metric_resolution: null`. Its conclusion was a reasonable inference from the only
evidence the harness returned. See *The harness withheld the rejection reasons*,
below.

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

### Where the responsibility actually sits

The participant made the immediate mistake. But calling it a typo understates
what the system did to earn it.

The discrepancy packet — which `CHALLENGE.md` names as the participant's primary
evidence — presents friendly display fields (`excess_fraction`,
`max_spacing_error`) while the evaluator requires canonical identifiers
(`reference.macro.excess_fraction`, `pattern.spacing_max_error`). The resolution
table confirms the canonical names are the real contract. RoboVision therefore
handed the agent a namespace translation problem that it had no need to create,
and then reported the failure in terms that did not point at it.

Attempt 1 is the same shape. The evaluator was right to refuse protected metrics
that do not exist, but its missing-metric branches offer none of the corrective
guidance the ambiguity branches beside them already do. Two cheap messages would
have closed both failures:

```
geometry.manifold is an invariant, not a metric. It is already enforced
by required_invariants and must not be repeated under protected.
```

```
Unknown metric reference.excess_fraction in ref:front. Available:
reference.macro.excess_fraction, reference.macro.deficit_fraction, ...
```

Neither helps a model solve the asset. Both make the protocol self-describing.

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


## Participant 2 — Nemotron 3 Ultra Free, clean and isolated

Run identity `nemotron-3-ultra-clean-01`. Unchanged v2, same shim, same prompt,
same 8-attempt budget, no knowledge of Participant 1.

**8 attempts / 0 accepted / 8 rejected / 8 verified rollbacks /
`budget_exhausted`.** Restore verified against the frozen signature and Q0. 214
correction-execution calls, 91 observation calls, 305 total, 23.83s. No manual
interventions. **Retained state is exactly A0** — every rollback restored, and the
final vector is identical to Q0 in every component.

That answers the question that mattered most: every configuration the participant
reported as "consistently fixable" existed only inside a candidate the vector gate
refused. The asset never carried any of it.

### What it got right

It **used the canonical names from its first attempt** —
`reference.macro.excess_fraction`, `reference.macro.deficit_fraction`,
`pattern.spacing_max_error`, each with the correct kind. Participant 1's
reference-namespace barrier did not stop it. So that barrier is real but not
universal.

And **its first attempt chose the minimum-change radius.** A0 places Bracket_0 at
r = 0.245804 and the other four at 0.204486 (from the public manifest's
`normalized_a0`; no sealed material was read). Attempt 1 moved Bracket_0 to
0.20449 — the four-member consensus — and Bracket_1 back to 72°. That is the
single-outlier repair, not the outward one. It took pattern spacing from 4.900687°
to 0.000073° and front excess from 0.139025 to 0.124274.

### Why that attempt failed, and what happened next

It bundled three objectives into one correction and declared an epsilon of 0.03 on
both reference targets:

| target | achieved | required |
| --- | --- | --- |
| `pattern.spacing_max_error` | 4.900614 | 1.0 ✓ |
| `ref:front` `reference.macro.excess_fraction` | 0.014751 | 0.03 ✗ |
| `ref:side` `reference.macro.deficit_fraction` | 0.001539 | 0.03 ✗ |

The geometry was right and improved every metric it touched. The attempt was
rejected because the participant promised more than one correct edit could
deliver, and vector acceptance does not give partial credit.

It then kept the correct radius through attempts 2–3 but added a 90° rotation of
all five brackets, which regressed its own declared protections (front deficit
0.017 → 0.099, side excess 0.182 → 0.260). From attempt 4 it abandoned 0.2045 and
moved all five **outward** to 0.246, thinned the mast and moved the counterweight.
That produced the front excess of 0.028 its note reports — while front deficit went
0.017 → 0.181 and side deficit 0.105 → 0.168. Excess had been converted into
deficit. Its own protections caught it every time.

So the provisional reading — that it failed to prefer the minimum-change
explanation — is **not supported**. It preferred it first, then abandoned it after
rejections that the correct radius had not caused. The failure is **credit
assignment across a bundled correction under vector rejection**: which edit in a
rejected bundle was responsible.

### Why it could not assign that credit

The harness withheld the answer. It saw `decision: reject` together with a
`targets_achieved` list that **included the pattern target it had met**. From its
side, attempt 1 met its main objective and was rejected for no stated reason. The
field it could see reported transient success; the field that explained the
rejection was never sent. Its "consistently fixable" is an accurate reading of
`targets_achieved`, not a hallucination.

It also listed `geometry.manifold` as a protected metric **in all eight attempts**
— the same invariant-as-metric confusion Participant 1 made on attempt 1. It never
learned otherwise, because the resulting `protected_metric_missing` was an
indeterminate cause, indeterminate causes were never returned, and every attempt
also carried a reject cause that outranked it.

Its stop note is preserved verbatim, including the belief that the object set
"cannot provide the required side geometry". That is a participant belief. The
source-assisted preflight reported the existing objects sufficient; that report
is the operator's and is not independently verified here.

## The harness withheld the rejection reasons

This is a defect in the benchmark harness, in code written for it at `a4dfccc`,
and it shaped **both** clean results.

`CHALLENGE.md` promises that after a rejection *"you try again with the evidence
from the failure"*. `runner.attempt` returns `decision`, `outcome`, `restored`,
`targets_achieved`, `epsilon_audit` and a fresh packet. It does not return
`reject_causes` or `indeterminate_causes` — the only fields that say what failed.
The shim passes that dict through unchanged. The contract promised the evidence
from the failure and the harness sent the evidence of partial success instead.

Consequences, now that it is visible:

- **Participant 1** could not see `target_metric_missing`, so it could not learn
  that `reference.excess_fraction` did not exist; a null resolution was the only
  signal and it read it the natural way. The earlier claim that the information
  was available to it is retracted above.
- **Participant 2** could not see which target fell short, which protection
  regressed, or that `geometry.manifold` was never a metric — so it could not
  separate a correct edit from the incorrect ones bundled with it.

Both runs remain valid **as runs of v2 as built**, and they are comparable with
each other because both were blinded identically. They are not measurements of
what either model can do when told why it failed. v2 is not modified: no further
participant runs against it, and the fix belongs to v3.

**This moves to the top of the v3 list.** Returning the causes is not a legibility
improvement like the others; it is the precondition for any of them to matter. A
self-describing error message is worthless if the harness never delivers it.

## Four runs, one pattern

| | access | attempts | accepted | retained faults fixed |
| --- | --- | --- | --- | --- |
| Preflight A — Muse | source, offline analytical | not budget-comparable | 4 predicted | 4 (operator-reported) |
| Preflight B — Spark | source, live harness | 4 | 1 | 1 |
| Participant 1 — MiMo | **clean, isolated** | 6 | 1 | 1 |
| Participant 2 — Nemotron | **clean, isolated** | 8 | 0 | 0 |

All four localised the `Bracket_1` angular fault — Participant 2 in its first
attempt alongside the correct `Bracket_0` radius. That is four reasoning clients,
two with no access to anything, agreeing that the radial-pattern discrepancy is
legible from the evidence alone.

The two clean participants failed at **different layers**:

| layer | Participant 1 | Participant 2 |
| --- | --- | --- |
| detect and correct the pattern angle | yes | yes, attempt 1 |
| pick the minimum-change radius | not attempted | **yes, attempt 1** — then abandoned |
| name the canonical reference metrics | no | **yes** |
| separate invariants from metrics | no (attempt 1), then yes | **no, all 8 attempts** |
| keep one objective per correction | mostly | no — bundled throughout |
| assign a rejection to the right edit | not tested | no — and was never told the causes |
| infer a 3D correction from a silhouette deficit | not established | no |
| safety and exact rollback | yes | yes, 8 of 8 |
| stopped | autonomously | budget exhausted |

Participant 2's zero is not a worse result than Participant 1's one in any useful
sense. It found the right repair first and could not keep it. The harness is the
reason it could not tell that the repair was the part that worked.

The divergence after that is the more useful half. Unlimited offline evaluation
solved everything; both live runs solved one.

The supported statement about the clean run is narrower than it is tempting to
make it:

> The observed failure was dominated by evidence-contract legibility rather than
> by failure to localise the remaining geometric faults.

It named the counterweight displacement and the `Bracket_0` radius in its stop
note, which is good evidence that perception and localisation worked. But those
attempts died on invalid metric identifiers **before any measurement was taken**,
so whether its specific geometric corrections would have succeeded was never
established. "The barrier was not spatial reasoning" is a stronger claim than the
evidence supports, and is not made here.

What can be said is that the bottleneck observed was in the language for
describing the world rather than in seeing it — and that is a considerably more
actionable finding than a model that could not see the faults at all.


## What v2 has already proven

A claim that could not be made before Participant 1:

> A clean, previously unfamiliar frontier model used only RoboVision's restricted
> public interface to inspect a 3D asset, independently identify a real spatial
> defect, generate an appropriate typed correction, have it quantitatively
> verified and committed, survive five unsuccessful attempts with exact rollback,
> preserve all hard geometry invariants, and stop autonomously.

That is not "AI can autonomously make professional 3D assets". It is evidence
that the core thesis holds at the first level: 3D correction competence transfers
through RoboVision to a model that did not build RoboVision. The failure is
encouraging precisely because the model did not fail to see the world — it got
tangled in our language for describing it, which is the easier problem.

## Participant 2, then v3

Participant 2 runs against **unchanged v2**. Fixing the interface first would buy
a nicer run and lose the only direct comparison available, so the freeze holds.

The A/B question is worth more than the fix:

- if the second model discovers the canonical names unaided and addresses further
  faults, Participant 1's failure was partly model-specific — the interface is
  usable but not equally legible to every client;
- if it independently reaches for `reference.excess_fraction` or something like
  it, that is **cross-model evidence that the interface is defective** rather
  than merely awkward.

Either answer is worth more than the repair.

### What v2 actually tested

v2 did not fully implement the interaction it claimed to test. `CHALLENGE.md`
promised the evidence from each failure, and the closed correction loop was
missing its negative-feedback channel entirely: the evaluator computed why every
candidate failed, and the harness never delivered it. So v2 is best read as an
accidental ablation —

> autonomous 3D correction when the model can see evidence of success but cannot
> see why a rejected correction failed.

Under that condition, the findings separate cleanly:

- **Transfer and perception: strong evidence.** Four reasoning clients, two of
  them clean, localised the `Bracket_1` angular fault; one clean model chose the
  minimum-change radius on its first attempt.
- **Execution safety: very strong evidence.** Five rollbacks for Participant 1,
  eight for Participant 2, with Participant 2's final vector exactly equal to Q0 —
  organic agent mistakes, not injected faults.
- **Verification: strong evidence.** Configurations that dramatically improved
  individual numbers, front excess included, also regressed protected reference
  dimensions, and the vector evaluator refused every one rather than letting one
  metric pay for another.
- **Adaptive reasoning: not properly tested.** The models were denied the
  failure explanations the contract promised them.

The headline is therefore not "1 of 6, 0 of 8". It is that both independent
models found genuine 3D faults through a restricted interface, the verifier kept
every bad or unproven candidate out of the asset, and the experiment exposed that
the adaptive loop had been withholding its own feedback.

### The sequence from here: one variable at a time

Every change below is plausibly good. Shipping them together would destroy the
ability to answer the question v2 has made most important: **how much capability
was hidden solely because the model was not told why it failed?** So they ship as
separate, ordered ablations against the same asset, Q0, references, prompt,
budget and tool surface.

**v3a — failure feedback only.** Built:
`benchmarks/correction-transfer-v3-feedback/`, benchmark id
`rvbench:correction-transfer/v3-feedback`. `submit_correction` additionally returns
an `evaluation` block holding the evaluator's existing `reject_causes`,
`indeterminate_causes`, `targets_achieved` and `invariants_checked`, exposed as
computed with no paraphrase — and **indeterminate causes are returned even when a
reject cause decided the outcome**, which is precisely what Participant 2 needed
eight times. `targets_achieved` in a rejected attempt is not relabelled; beside
`outcome: rolled_back`, the full evaluation should speak for itself, and adding an
interpretation would be a second change.

The change is gated on the benchmark's own manifest
(`harness.return_failure_causes`), so v2 keeps its original behaviour and its
runs stay reproducible exactly as built. Proved live over the real stdio shim with
a probe that authors nothing:

```
correction-transfer-v2            evaluation: NOT RETURNED
correction-transfer-v3-feedback   reject_causes:        insufficient_target_improvement / pattern.spacing_max_error
                                  indeterminate_causes: protected_metric_missing / geometry.manifold
                                  scene unchanged after probe in both
```

Carried from v2 unchanged and checked: the normalized A0 signature, the Q0
vector, the hidden commitments, the budget, the known limits, the required
invariants, and the digest of every participant file. The answer key is v2's,
reused by name (`private_material`) rather than copied, so there is still exactly
one sealed recipe and it is still the one the published hash covers.

Two ablation caveats, disclosed rather than engineered away:

- `CHALLENGE.md` is byte-identical to v2's, **including its v2 heading**. It
  already promised the feedback, so v3a is the harness keeping a promise the
  document had made. The cost is a participant file naming the wrong version.
- The participant-facing `MANIFEST.json` honestly records the harness change, so
  a v3a participant that reads it is told to expect failure causes. v2
  participants were told the same by `CHALLENGE.md`, so both conditions were
  promised the feedback and only delivery differs — but the manifest line is a
  small additional prime, and it is noted here rather than hidden by an
  incomplete manifest.

Fresh MiMo and Nemotron contexts run v3a — new sessions, not the v2
conversations, same model versions where available. Sampling variance between
fresh runs means this is not a high-powered controlled experiment, but the
qualitative behaviour answers specific questions:

- **Participant 1's model:** does `target_metric_missing` lead it to inspect the
  certificates and find `reference.macro.excess_fraction` itself? If it still
  cannot, canonical namespace legibility genuinely needs v3b.
- **Participant 2's model:** after an attempt-1 equivalent it would now see the
  pattern target met, both reference targets short of an epsilon set too high,
  `geometry.manifold` not a metric, and the scene restored. Resubmitting the
  bracket repair alone would be direct evidence that missing feedback, not 3D
  reasoning, suppressed its v2 performance. Abandoning the correct radius even
  with the causes in hand would mean credit assignment is a real model or
  interface problem.

**v3b — contract legibility, only after v3a.** Canonical metric identifiers
carried in the packet beside the friendly fields; invariants separated from
targetable and protectable metrics; missing-metric errors that list the
certificate's valid metrics and a conservative near-name suggestion, with no
fuzzy auto-correction.

**v3c — attribution, only if still needed.** Per-operation credit assignment in a
rejected bundle, and image-space discrepancy attributed back to the responsible
object and a world-space direction. Deferred deliberately: per-operation
attribution needs intermediate measurements and is causally ambiguous when edits
interact, and ordinary failure feedback may make it unnecessary — a model told
why a bundle failed may simply start submitting one hypothesis per correction,
which is the behaviour the loop was designed for.

**Separately, after those:** make the resolution rule real, accepting against
`max(declared epsilon, known metric resolution)` so "below resolution is
unproven" is enforced rather than audited. It changes no v2 outcome, but it is a
change to acceptance and gets its own version.

### The original v3 list, kept for the record

0. **Return the rejection reasons.** `submit_correction` must return
   `reject_causes` and `indeterminate_causes`, and indeterminate causes must not be
   hidden behind a reject cause that outranks them. `targets_achieved` in a
   rejected attempt must say plainly that the achievement was not retained. This
   is the precondition for every item below: a self-describing error is worthless
   if the harness never sends it.

   Participant 2 adds two items that follow from it:

   - **Per-operation attribution in a rejected bundle.** When a multi-edit
     correction is rejected, the participant cannot tell which edit caused it.
     Per-operation Q1 deltas, or a recommendation to submit one objective per
     correction, would have let it keep the correct radius.
   - **Image-space discrepancy attributed back to 3D.** A sector deficit says
     where the silhouette is wrong, not which object projects nearest that
     region, which direction of movement would cover it, or where depth leaves
     it ambiguous.

1. **One canonical metric namespace everywhere.** The discrepancy packet should
   carry the evaluator's exact identifier beside the friendly field, so an agent
   can copy `metric` and `kind` straight into a correction:

   ```json
   {"excess_fraction": {"metric": "reference.macro.excess_fraction",
                        "kind": "ref:front", "value": 0.139025,
                        "resolution": 1.52587890625e-05,
                        "direction": "lower_better"}}
   ```

2. **Machine-actionable missing-name errors.** `target_metric_missing` and
   `protected_metric_missing` should return the certificate's valid metrics and a
   conservative exact or near-name suggestion. No fuzzy auto-correction — the
   participant resubmits deliberately, and the record shows it chose.

3. **Invariants separated unmistakably** from metrics in the observation:
   `required_invariants` (automatically enforced hard gates, never repeated under
   `protected`), `targetable_metrics`, `protectable_metrics`.

4. **Resolution semantics made real.** Today the evaluator decides and
   `_epsilon_audit()` records afterwards. v3 should accept against
   `max(declared epsilon, known metric resolution)`, so "below resolution is
   unproven" becomes an invariant of the system rather than a sentence in a
   document.

Separately, and as analysis instrumentation rather than an agent capability: the
facade should record *which* read methods a participant called, not only how
many. Its absence is why this document can say what Participant 1 got wrong but
not how it got the pattern name right.

## v3a result 1 — MiMo V2.5 Free, fresh context, failure causes returned

Run identity `mimo-v2.5-v3a-01`, against `rvbench:correction-transfer/v3-feedback`.
Same asset, Q0, references, prompt, tools and budget as its v2 run; a new session.

**7 attempts / 2 accepted / 3 rejected / 2 indeterminate / 5 verified rollbacks /
stopped `no_worthwhile_correction_remains`.** 150 correction-execution calls, 121
observation calls, 271 total, 21.8s. No manual interventions. The operator's chat
summary said three accepted; the harness records two, and so does the
participant's own stop note.

| MiMo | v2 — causes hidden | v3a — causes returned |
| --- | --- | --- |
| attempts | 6 | 7 |
| accepted | 1 | **2** |
| bracket angle fixed | yes | yes |
| bracket radius retained | no | **yes** |
| counterweight improvement retained | no | **yes** |
| front `silhouette_iou` | 0.862899 | **0.913085** |
| side `silhouette_iou` | 0.759896 | **0.764472** |
| side `deficit_fraction` | 0.102966 | **0.085478** |
| side `excess_fraction` | 0.180470 | 0.196279 (worse) |
| `coverage.observed_fraction` | 0.915716 | **0.961591** |
| hard invariants | pass | pass |

### The two accepted attempts, exactly

| metric | A0 | after attempt 3 (brackets) | after attempt 7 (counterweight) |
| --- | --- | --- | --- |
| `pattern.spacing_max_error` | 4.900687° | 0.000103° | 0.000103° |
| front `silhouette_iou` | 0.862899 | 0.882969 | 0.913085 |
| front `excess_fraction` | 0.139025 | 0.124274 | 0.088660 |
| front `deficit_fraction` | 0.017136 | 0.007301 | 0.005960 |
| side `silhouette_iou` | 0.757604 | 0.759896 | 0.764472 |
| side `excess_fraction` | 0.182009 | 0.180470 | **0.196279** |
| side `deficit_fraction` | 0.104505 | 0.102966 | 0.085478 |
| coverage | 0.915716 | 0.915716 | 0.961591 |

Attempt 3 moved Bracket_0 to r = 0.20449 — the four-member consensus — and
Bracket_1 to 72°, and improved every reference metric it touched. Attempt 7 moved
the counterweight and is where most of the front improvement came from.

### The mechanism is in the record, not inferred from the score

Two sequences show exactly the pattern v3a exists to test — failure returned,
participant changes the offending claim, resubmission succeeds:

**Attempts 1 → 2 → 3, brackets.** Attempt 1 had the right geometry (spacing
4.9007° → 0.0158°) and went indeterminate: it targeted
`pattern.spacing_matches_declared`, which is an invariant rather than a metric,
and protected `geometry.manifold`. Attempt 2 refined the geometry to the exact
consensus radius and went indeterminate again, on two misnamed targets
(`reference[front].silhouette_iou`) and all five invariants listed as protected.
Attempt 3 submitted **identical geometry** with only the one target that had been
achieved, and was accepted.

**Attempts 5 → 7, counterweight.** Attempt 5 moved the counterweight to
(0, −0.18, −0.1), achieved a side-deficit improvement of 0.017488 against a
declared epsilon of 0.02, and was rejected. Attempt 6 tried a different position
and did worse. Attempt 7 resubmitted attempt 5's **identical geometry** with an
epsilon of 0.01, and was accepted.

In both, the geometry did not change between the failed and the accepted attempt;
only the claims did. That is direct evidence that the geometry was right and the
barrier was the correction contract.

Canonical naming was learned, eventually. Attempts 1–2 used non-canonical names;
attempt 3 avoided reference targets altogether; attempts 4–7 used
`reference.macro.deficit_fraction` correctly. The first `target_metric_missing`
it ever received for a reference metric arrived after attempt 2. How it then
found the canonical name is not observable, because the facade still records how
many observation calls a participant makes but not which.

### Two cautions before calling this a clean win

**The feedback taught it to drop protections, not to correct them.** After
attempt 2's five `protected_metric_missing` causes it declared **no protections
at all** for attempts 3–7. That removed the invariant confusion, and it also
removed the reference protections it had declared in attempt 1. Attempt 7 then
regressed side `excess_fraction` from 0.180470 to 0.196279, and nothing refused
it, because nothing had been declared to protect it. Required invariants are
enforced regardless and held throughout; the vector gate protects only what a
participant chooses to declare. Net side agreement still improved (IoU up,
deficit down more than excess rose), so this is a trade the contract permits, not
a corruption — but it is a trade made by an agent that had just learned
protections were the thing getting it refused. Whether untargeted reference
metrics should carry a default protection is a design question for later, and is
not built.

**Attempt 7 set its epsilon after the fact.** With causes returned, a participant
can see exactly how much an attempt achieved and resubmit the same edit claiming
a little less. That is legitimate here: 0.017488 is roughly 1,146 times the
metric's resolution of 1.526e-05, so it is a real, proven improvement, not the
sub-resolution gaming the epsilon audit guards against. It does change what
epsilon means — from a prediction to a calibrated claim, at the cost of an
attempt — and the later resolution-gate change should be designed with this in
mind.

### What this supports

For one model family, on the same asset, Q0, prompt, tools and budget, returning
the evaluator's failure causes coincided with a large qualitative change: the
correct bracket radius and a counterweight improvement were retained where
neither was in v2. The two identical-geometry resubmissions make the mechanism
visible. One stochastic rerun per condition does not establish that the whole
numerical difference is caused by the feedback, and the gain came partly from the
participant claiming less — dropping protections and calibrating an epsilon — as
well as from correcting what it claimed.

Participant 2's model runs v3a next, unchanged. Its v2 run found the right repair
on attempt 1 and abandoned it; whether returned causes let it isolate and keep
that edit is the replication that would make this more than one model's result.

## Incident — participant sessions routed to the wrong run (2026-09-10)

The first Nemotron v3a session is **not a result**. Every `submit_correction` came
back `RUN_STOPPED no_worthwhile_correction_remains` — the stop reason of the
already-finished MiMo v3a run, not anything of Nemotron's.

**Cause.** OpenCode Desktop launched a new `rvbench` shim for each participant and
never stopped the old ones; four were live at once under one server name, so the
routing of `rvbench_*` calls among them was undefined. Every participant folder's
`opencode.json` was correct. The Nemotron session was served by the stale MiMo v3a
shim.

**What it touched.** A read-only audit of every run against its own finalized
report found two:

- the finished **MiMo v3a** run: observations added to its counter, `packet.json`
  rewritten, and — because `stop` did not check whether a run was already stopped —
  its sealed stop reason and note **overwritten** with the Nemotron session's. No
  attempt was added and the scene was never mutated; the scene was verified to be
  exactly A0 afterwards.
- the finished **v2 MiMo** run: MiMo v3a's own session opened with about 10
  observation calls through the stale v2 shim, 33 seconds before its correct shim
  started.

**MiMo v3a stands.** The evidence the stale v2 shim returned was identical to its
own Q0 — same scene at the same moment, byte-identical brief — and its first
attempt began eight minutes after its correct shim was up; all seven attempts carry
the v3a-only evaluation fields. Its observation count is undercounted by about 10,
recorded rather than edited into a finalized report. No v2 result changes.

**Records.** As-found copies with hashes, a repair log restoring each touched file
to its own finalized report's value, and no finalized report modified:
`artifacts/benchmark/correction-transfer-v3-feedback/INCIDENT-2026-09-10-run-identity/`.

**Fixes.** Transactions were strict; the run lifecycle around them was not.

1. A stopped run is immutable — a second `stop` changes nothing.
2. A used run identity cannot be restored over; a new experiment needs a new id.
3. A shim refuses to serve a finished run, and binds to the scene before serving:
   the editor must hold exactly the state that run left behind — A0 by normalized
   signature before any attempt, the last attempt's authored fingerprint after.
4. Every correction re-checks that binding before any transaction opens.
5. `health` carries a non-secret `benchmark_run` identity beside the host's report,
   so a misrouted session can see it at once.

None of these touch evaluation or feedback, so they do not confound the v3a
ablation. Item 5 is the one participant-visible addition; MiMo v3a ran without it,
and it names a run rather than carrying any evidence about the asset.

*As planned at the time:* Nemotron's v3a run was to take place under
`nemotron-3-ultra-v3a-02` in a new folder. **It did not** — the session was
launched from the older folder and ran as `nemotron-3-ultra-v3a-01`. Why that run
is still valid is set out under *v3a result 2*, below; `-02` was retired unused.

## v3a result 2 — Nemotron 3 Ultra Free, fresh context, failure causes returned

**Which run this is.** It ran as `nemotron-3-ultra-v3a-01`, launched from the older
folder `D:\RVBench-Nemotron-02`, not the `-02` identity and `-03` folder prepared
after the incident. It is still a valid v3a run, and every condition was checked
rather than assumed: the transcript carries no trace of the earlier misrouted
session; its first `health` reported `attempts_used: 0, stopped: false`; the shim
bound to the scene before serving, and the session's first observation is
identical to the frozen Q0; and before finalizing, the scene was bound to the run's
last recorded state. The deviation is in the run's name, not in its conditions.

**7 attempts / 0 accepted / 7 indeterminate (one invalid) / every candidate
restored / stopped `evidence_insufficient` with one slot unused.** 141
correction-execution calls, 91 observation calls, 232 total. The retained state
equals A0 exactly.

Attempt 1 is invalid: its `advisory` field was malformed, the evaluator refused it
*after* the bracket mutations had been delivered, and the fail-closed path rolled
them back — the second time that machinery has fired on an organic participant
error rather than an injected one.

### The feedback worked, and was not enough

After attempt 2 it received eleven `*_metric_missing` causes, and from then on
**every attempt changed the identifiers** — the returned causes were read and acted
on. It cycled through every plausible namespace except the right one:

| attempt | pattern target tried | reference target tried |
| --- | --- | --- |
| 2 | `max_spacing_error` | `excess_fraction` |
| 3 | `max_spacing_error` | `silhouette_iou` |
| 4 | `pattern[brackets].max_spacing_error` | `reference[front].excess_fraction` |
| 5 | `pattern.max_spacing_error` | `reference.excess_fraction` |
| 6 | `brackets.max_spacing_error` | `front.excess_fraction` |
| 7 | `pattern.brackets.max_spacing_error` | `reference.front.excess_fraction` |
| canonical | `pattern.spacing_max_error` | `reference.macro.excess_fraction` |

Each was correctly refused as missing. It submitted the same bracket repair every
time, and because no target ever resolved, **that repair was never measured** — so
this run establishes nothing either way about its geometry. (Its v2 run measured the
same repair improving every metric it touched.)

### Both the interface and the model contributed

Its stop note says the naming convention was not discoverable. That is too strong.
`CHALLENGE.md`'s own example uses `reference.macro.excess_fraction` and
`pattern.spacing_max_error`. The transcript mentions the first **18 times** and the
second twice, and it submitted neither. So:

> RoboVision made the canonical namespace unnecessarily hard to map from its
> primary evidence, and the participant failed to use the one explicit canonical
> example it had.

The pattern case shows how unnecessary the difficulty is. The packet displays
`max_spacing_error`; the evaluator wants `spacing_max_error` — the same words in the
other order. None of its six guesses tried reordering, and nothing it could see
suggested it should.

## v3a, both models

| | v2 — causes hidden | v3a — causes returned |
| --- | --- | --- |
| **MiMo** | 1 accepted; bracket angle retained | **2 accepted**; bracket angle and radius, and a counterweight improvement retained |
| **Nemotron** | 0 accepted; correct first repair abandoned after an unexplained rejection | 0 accepted; causes read and acted on, canonical names never found |

(The operator's summaries have twice given MiMo v3a three accepted attempts. The
harness records two, attempts 3 and 7, and so does the participant's own note.)

Returning failure causes is **valuable but not universally sufficient**. MiMo turned
them into retained corrections. Nemotron consumed them correctly — its behaviour
changed on every attempt because of them — but "that name does not exist" does not
say which name does. Nemotron's failure moved from credit assignment in v2 to naming
in v3a, and the bottleneck it now sits at is more basic than attribution: whether a
model can address the measurement it is looking at.

Two models, one run each per condition. These are qualitative findings, not effect
sizes.

**v3a is complete.** The next ablation is **v3b — canonical metric addresses in the
evidence**, changing nothing else: every participant-facing metric carries the exact
`{metric, kind}` a correction must name, beside its friendly field, so that what a
model reads is what it can submit. Whether invariant/metric separation belongs in the
same ablation or waits for its own is still open.

## v3a result 3 — Muse, fresh isolated context

**Classification: fresh isolated Muse v3a diagnostic; same model family previously
used in the source-assisted preflight** (Preflight A). Recorded before the run. This
session had no prior conversation, repository, web, shell or hints, and its first
`health` confirmed `muse-v3a-clean-01` with `attempts_used: 0, stopped: false`.

**8 attempts / 8 accepted / 0 rejected / 0 indeterminate / `budget_exhausted`.**
The harness ended the run after the eighth accepted attempt; the participant never
called `stop`, and attempt 8 was still improving side deficit by 0.020 — so the
budget, not the evidence, was the binding limit. 169 correction-execution calls, 169
observation calls, 338 total.

| attempt | edit | target | before → after |
| --- | --- | --- | --- |
| 1 | Bracket_0 to r = 0.204486, Bracket_1 to 72° | `pattern.spacing_max_error` | 4.900687° → 0.000004° |
| 2 | counterweight X 0.031 → 0 | front `reference.macro.excess_fraction` | 0.124274 → 0.086574 |
| 3 | mast width → 0.10 | front excess | 0.086574 → 0.039189 |
| 4 | counterweight Y/Z | side excess | 0.117236 → 0.099468 |
| 5 | mast width → 0.09 | front excess | 0.032931 → 0.007152 |
| 6 | counterweight raised | side deficit | 0.062535 → 0.049245 |
| 7 | counterweight further −Y | side deficit | 0.049245 → 0.034135 |
| 8 | counterweight further −Y | side deficit | 0.034135 → 0.013990 |

| | A0 | final |
| --- | --- | --- |
| front `silhouette_iou` | 0.862899 | **0.997619** |
| side `silhouette_iou` | 0.757604 | **0.967467** |
| front excess / deficit | 0.139025 / 0.017136 | 0.001192 / 0.001192 |
| side excess / deficit | 0.182009 / 0.104505 | 0.019166 / 0.013990 |
| coverage | 0.915716 | 0.977791 |
| hard invariants | pass | pass |

Every improvement cleared its metric's resolution by orders of magnitude.

### v3a's variable never engaged

**No attempt returned a reject or indeterminate cause.** The one thing v3a changes —
returning failure causes — never took part in this trajectory. So this run is not
evidence that failure feedback helped. It is evidence of something else:

> the existing RoboVision interface was already sufficient for a capable reasoning
> client to find the geometry, find the metric contract, attribute error to
> individual objects, and get every correction it proposed verified and committed.

Its manifest did name the ablation, so it could have read that feedback was
available; it never received any, because it never failed.

### How it avoided Nemotron's failure

Per the transcript — the facade still counts observation calls without recording
which — it did not rely on the flattened discrepancy packet. It called the truth
tools directly. `truth.pattern` returns the certificate metric
`pattern.spacing_max_error` and the actual gap sequence (76.90, 67.10, 72.00, 72.00,
72.00), and `truth.reference` returns `reference.macro.*`. It submitted those names
from attempt 1.

So the canonical namespace is **discoverable** through the composable interface,
and badly surfaced by the primary packet. That sharpens Nemotron's result: it did not
only need a better error message; it did not exploit the measurement tools that let
Muse resolve the ambiguity itself.

It also did its own 2D-to-3D attribution: `truth.reference` run on object subsets —
mast alone, counterweight alone, mast with counterweight — to decide which object was
responsible for which silhouette discrepancy. The attribution subsystem listed as v3c
is therefore **not needed yet**; the existing primitives were enough for a strong
client.

### Not answer replay, on the available evidence

The trajectory is closed-loop: the mast went to 0.10, was measured, then to 0.09; the
counterweight took four measured steps. The operator reports that Preflight A's
counterweight was near (0, −0.168, −0.193), where this run arrived iteratively at
(0, −0.155, −0.190). That comparison is the operator's, against a contaminated
preflight, and not against the sealed answer key, which has not been read. The
sandbox also held: the transcript shows OpenCode denying the participant's attempts
to read images from a directory outside its folder.

### A new policy concern

Attempts 1–6 declared **no protections**. An accepted correction could have improved
its target while regressing an undeclared DAT dimension, provided invariants and
locality held. Muse improved everything, so nothing went wrong, and by attempts 7–8 it
protected front excess and deficit while working on the side view. MiMo's v3a run
dropped protections too, and there an undeclared metric did regress. Two of three v3a
clients accepted corrections with nothing declared to protect.

Recorded for later, **not for v3b**: a **retained-evidence guard** — optionally
refuse a correction that materially regresses previously established high-confidence
truth unless the participant explicitly authorises the trade.

## v3a, all three models

| | outcome | where it ended |
| --- | --- | --- |
| **MiMo** | 2 accepted | used returned causes to keep corrections v2 lost |
| **Nemotron** | 0 accepted | read the causes, never found the canonical names |
| **Muse** | **8 accepted** | never failed, so never needed the causes; used the truth tools directly |

The same fixed tool surface produced radically different outcomes depending on how
well the model used the measurement primitives. The bridge is no longer obviously the
bottleneck. Muse ran the whole intended loop — inspect, hypothesise, isolate evidence,
make a typed edit, verify, commit, inspect the residual — eight times in a row.

**v3b is still worth building, for a different reason than before.** Not because
RoboVision requires it — Muse shows it does not — but to measure how far closing an
unnecessary legibility gap raises MiMo and Nemotron toward what Muse already did.

## v3b — metric-legibility ablation

Benchmark `rvbench:correction-transfer/v3b-legibility`, package
`benchmarks/correction-transfer-v3b-legibility/`, derived from frozen v3a.

**The one change.** Each section of the discrepancy packet gains an `addresses` map
from friendly field to the exact `{metric, kind}` pair a correction's `targets` or
`protected` entry takes — so what a participant reads is what it can submit.

| section | friendly field | address |
| --- | --- | --- |
| reference | `silhouette_iou`, `excess_fraction`, `deficit_fraction`, `aspect_error` | `reference.macro.*` |
| reference | `contour_mean_distance` | `reference.contour.mean_distance` |
| reference | `worst_sector_magnitude` | `reference.contour.worst_sector_net` |
| patterns | `max_spacing_error` | `pattern.spacing_max_error` |
| patterns | `max_angular_deviation`, `dimension_cv` | `pattern.*` |
| dimensions | `size[0..2]`, `largest` | `spatial.dimension_x/y/z`, `spatial.largest_dimension` |
| coverage | `observed_fraction` | `coverage.observed_fraction` |

Three rules keep it to one variable:

- **additive only** — no existing value, key or type changes;
- **only `{metric, kind}`** — no direction, resolution or guidance prose;
- **provably copyable** — an address is emitted only if the evaluator's own lookup
  resolves it to the exact value displayed beside it. Descriptive detail (sectors,
  region geometry, counts from invariant evidence, gap indices, labels) is not a
  certificate metric and is not addressed.

**Unchanged from v3a:** the asset, faults, references, attempt budget, evaluator,
correction semantics, tool surface and every tool description, the prompt,
`CHALLENGE.md` (byte-identical, still headed v2), failure feedback, invariant
presentation, missing-metric wording, the epsilon rule and protection behaviour.
The normalized A0 signature, Q0 vector, hidden commitments and participant-file
digests were checked identical to v3a's. As before, the participant-facing manifest
honestly records the change, which is a small disclosed prime.

**Proved live over the real stdio shim.** A v3a packet carries no addresses; a v3b
packet carries all fourteen. A probe that authors nothing, whose targets and
protections were copied verbatim from the v3b packet's addresses, resolved every
one: both targets were measured and refused as `insufficient_target_improvement`,
with **no `*_metric_missing` cause**. The scene was unchanged. The addresses are
invisible to the Q0 comparison vector, so restore verification is unaffected.

### Pre-registered measures

Recorded before any v3b participant runs, so they cannot be chosen after seeing the
results. v3b's question is mechanistic, not only a score:

1. **first resolved attempt** — the attempt at which every declared target and
   protection first resolves in the evaluator;
2. **protocol cost** — the total number of `*_metric_missing` causes across the run;
3. **copying** — whether submitted `{metric, kind}` pairs match packet addresses
   verbatim;
4. then, as before: accepted, rejected and indeterminate attempts, the retained
   vector, and the stop reason.

Compared against each model's own v3a run: MiMo's first attempt whose targets all
resolved was attempt 3, and Nemotron's never came.

### Order

Fresh MiMo first (`mimo-v2.5-v3b-01`, `D:\RVBench-MiMo-v3b-01`), then fresh Nemotron
(`nemotron-3-ultra-v3b-01`, `D:\RVBench-Nemotron-v3b-01`) — Nemotron restored only
after MiMo is finalized. A Muse v3b run is deferred: Muse found the canonical names
itself in v3a, so it adds least to v3b's question, and is worth running afterwards
only as a regression check on whether the more explicit packet gets in the way of a
model that already interrogates the truth tools.

After v3b this fixture is retired as the basic correction-transfer benchmark. Muse
reached front IoU 0.998 on it with budget left; the next benchmark should be
designed for interacting faults, ambiguity, attribution and planning.

## v3b result 1 — MiMo V2.5 Free, fresh context, canonical addresses in the packet

Run identity `mimo-v2.5-v3b-01`. First `health` confirmed
`v3b-legibility / mimo-v2.5-v3b-01 / attempts_used: 0 / stopped: false`.

**8 attempts / 2 accepted / 6 rejected / 0 indeterminate / `budget_exhausted`.** 173
correction-execution calls, 112 observation calls, 285 total.

### The pre-registered measures

| measure | MiMo v3a | MiMo v3b |
| --- | --- | --- |
| first attempt whose targets and protections all resolve | 3 | **1** |
| `*_metric_missing` causes across the run | 9 | **0** |
| submitted pairs not copied verbatim from a packet address | — | **none** |

Every `{metric, kind}` pair it submitted, in every attempt, matches a packet address
exactly. **All eight attempts reached real geometric evaluation.** That is the
mechanistic result v3b exists to test, and it is unambiguous for this model: the
namespace tax is gone.

### Retained state

| | A0 | final |
| --- | --- | --- |
| front `silhouette_iou` | 0.862899 | 0.911540 |
| front excess / deficit | 0.139025 / 0.017136 | 0.073014 / 0.021904 |
| side `silhouette_iou` | 0.757604 | 0.834738 |
| side excess / deficit | 0.182009 / 0.104505 | 0.112339 / 0.071489 |
| `pattern.spacing_max_error` | 4.900687° | 0.000832° |
| coverage | 0.915716 | 0.969582 |
| hard invariants | pass | pass |

The side result is better than MiMo v3a's (0.764). That numerical difference is not
attributed to v3b: these are fresh stochastic runs, one per condition. The causal
evidence for v3b is the measures above.

### The trajectory

- **1 → 2, brackets.** Attempt 1 made the correct bracket repair and was measured.
  It was rejected only because it asked front excess to improve by 0.02 and it
  improved by 0.014751 — and the returned cause said exactly that. Attempt 2 kept
  the geometry, asked for 0.01, and was accepted.
- **3, mast and counterweight together.** Mast to 0.08 and counterweight Y to
  −0.15. Front excess fell sharply, but the mast was too thin: front deficit
  regressed past its protection, and the side target fell short. Rolled back.
- **4, moderated.** Mast 0.10, counterweight Y −0.20. Accepted: side deficit
  0.102966 → 0.071489, front excess 0.124274 → 0.073014.
- **5, three variables at once.** Mast, counterweight Y and Z together; rejected,
  and the participant attributed the failure to Z. Plausible, not established —
  three things changed.
- **6, one variable.** Counterweight Y alone to −0.28; clearly worse (side deficit
  regressed, side IoU fell past its protection). Rolled back.
- **7, Y and Y-scale together.** Rejected on both targets.
- **8, the counterweight's X offset.** It noticed the +X offset contradicts the
  brief's offset on −Y and set X to 0. Front excess improved by 0.033825 against a
  requested 0.01 — **that target passed**. The attempt was rejected solely because it
  also required `reference.contour.mean_distance` to improve by 0.001, and it improved
  by 0.000971. A correction that met its main target was discarded by an extra claim.

**On attempt 8's cause, precisely.** The participant's closing reasoning says it
was rejected because the epsilon was below the metric's resolution. The recorded
reject cause is `insufficient_target_improvement` — 0.000971 against a declared
0.001. Resolution did not decide it. The epsilon audit does show that both the
declared epsilon and the achieved change were below the contour metric's resolution
(0.002762): the audit flagged it, and did not gate it. That is the outstanding
mismatch between `CHALLENGE.md`'s wording and the implementation, now met
organically by a participant.

**The participant's cowl theory** — that the residual may involve "the cowl or other
geometric features not captured by the fault model" — is preserved as its belief and
is not a finding. The public brief names the possible faults as bracket angle,
bracket radius, mast thickness and counterweight displacement; the counterweight
displacement was simply not fully localised within the budget.

### What it shows

With the protocol out of the way, MiMo's errors became **3D hypothesis design and
experimental attribution**: bundled edits whose effects could not be separated
(attempts 3, 5, 7), and bundled acceptance claims that turned a real improvement
into a rejection (attempt 8). The all-or-nothing vector gate did exactly what it was
told each time; the model made its own contracts harder to satisfy than they needed
to be.

MiMo did not become Muse — 2 of 8 accepted against Muse's 8 of 8 on v3a, with
front / side IoU 0.912 / 0.835 against 0.998 / 0.967. With the interface friction
removed for this model, the benchmark is increasingly measuring reasoning quality
rather than API trivia. Nemotron's v3b run is the replication that matters: in v3a
it was almost entirely blocked by name discovery.

