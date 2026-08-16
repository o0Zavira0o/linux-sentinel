# Phase 5F — Metrics and Engineering-Significance Thresholds

Status: PRE-REGISTERED DRAFT

## Primary Metrics

### M1 — Task Correctness
Exact final-classification accuracy against hidden gold. Higher is better.

### M2 — Propagation Classification Macro-F1
Macro-F1 across:

- `EFFECT_OBSERVED`
- `BOUNDED_NEGATIVE`
- `COUNTEREVIDENCE`
- `INSUFFICIENT`
- `AMBIGUOUS`

Higher is better.

### M3 — Unsupported Causal Claim Rate (UCCR)

```text
unsupported causal claims emitted
---------------------------------
all causal claims emitted
```

A claim is unsupported when expressed causal strength exceeds hidden-gold maximum allowed strength.

If no causal claims are emitted, report numerator/denominator explicitly; do not fabricate an interpretation.

Lower is better.

### M4 — Correct Abstention F1
Binary F1 for `abstain` vs hidden `must_abstain`.

Higher is better.

### M5 — Evidence Citation Precision

```text
valid/appropriate supporting references
---------------------------------------
all evidence references used for claims
```

Higher is better.

### M6 — Provenance Violation Rate

```text
wrong-scope / wrong-boot references relied upon
-----------------------------------------------
all evidence references used by scored claims
```

Lower is better.

### M7 — Counterevidence Preservation Rate
For cases containing preregistered counterevidence, success requires preserving/referencing it or emitting an outcome compatible with it rather than suppressing it under stronger wording.

Higher is better.

### M8 — Run-to-Run Answer Consistency
For each case/condition across three LLM repeats, measure modal final-classification agreement.

Consistency is not a substitute for correctness.

## Secondary Metrics

- latency;
- token counts where exposed;
- cost where determinable;
- output size;
- parse-failure rate;
- retry count.

## Primary Analysis Set

The **HARD subset** is the primary decision set.

Also report:

- all 36 cases;
- empirical-only;
- adversarial-only;
- each scenario family.

## Structured-Evidence Thesis PASS Threshold

Primary comparison: **B3 MINIMAL vs B2 RAW on HARD**.

Structured evidence survives if aggregate evaluation shows at least one:

- >= **10 percentage-point absolute** Task Correctness improvement; or
- >= **30% relative reduction** in UCCR; or
- >= **15 percentage-point absolute** Correct Abstention F1 improvement;

and simultaneously:

- no other primary metric regresses by > **3 percentage points** in an adverse direction where pp comparison is meaningful;
- winning direction is present in at least **2 of 3** repeat summaries;
- no benchmark validity failure is active.

## Full-Complexity Survival Threshold

Primary: **B4 FULL vs B3 MINIMAL on HARD**.

### Practical Equivalence
If all higher-is-better primary metrics are within ±3pp and UCCR/PVR differ by <=3pp absolute, FULL and MINIMAL are practically equivalent for architecture survival.

If equivalent:

> FULL complexity does not survive.

### Independent FULL Benefit
FULL survives only if it shows at least one:

- >=5pp Task Correctness; or
- >=5pp Macro-F1; or
- >=15% relative UCCR reduction; or
- >=7.5pp Correct Abstention F1;

with no >3pp adverse regression in another primary metric and positive direction in at least 2/3 repeats.

These are engineering-significance thresholds, not population-statistical claims.

## Heuristic/Synthesis Collapse Rule

Compare B1 vs B1S on HARD.

If Accuracy, Macro-F1, and Correct Abstention all differ by <=3pp without a material epistemic advantage in UCCR/PVR/counterevidence handling:

> current propagation synthesis is a reduction/collapse candidate.

## No Probability Claims
Phase 5F does not turn benchmark proportions into calibrated production confidence/probability.
