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
