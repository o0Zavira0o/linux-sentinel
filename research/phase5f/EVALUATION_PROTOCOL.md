# Phase 5F — Evaluation Protocol

Status: PRE-REGISTERED DRAFT — freeze before scored results

## 1. Objective

Test whether Sentinel-X evidence structure changes the quality and epistemic safety of operational reasoning compared with simpler alternatives.

## 2. Evidence Conditions

For each underlying case export three blinded views:

### RAW
Bounded raw systemctl/journal/necessary context with stable evidence references.

### MINIMAL
Only preregistered factual typed facts:

- case/environment/boot scope;
- observations;
- topology relations;
- incident timeline;
- coverage/missingness;
- provenance;
- intervention context only for explicit experiment-interpretation tasks.

MINIMAL is an evaluation projection, not a new public API.

### FULL
Current applicable frozen Phase-5 evidence for the same case, including existing derived evidence/provenance records when naturally applicable.

FULL must not receive hidden labels or expected outcomes beyond evidence legitimately present.

## 3. Corpus v1

Target: **36 scored cases**.

### Empirical cases — 16
- 8 independent controlled `Requires` executions: clear observed downstream effect;
- 8 independent controlled `Wants` executions: bounded-negative / declared topology with no observed downstream anomaly under sufficient coverage.

These validate real-system acquisition and provide sanity/reference conditions. They are not treated as novel systemd research.

### Adversarial evidence cases — 20
Derived from empirical evidence under frozen transformation rules:

- 4 insufficient-coverage cases;
- 4 reverse/counterevidence cases;
- 4 temporal-distractor cases;
- 4 cross-boot/provenance-invalid cases;
- 4 multiple-candidate ambiguity cases.

Derived adversarial cases are never counted as independent live experiments.

## 4. Primary HARD Subset

To prevent simple documented Requires/Wants cases from dominating the conclusion, the primary decision set contains:

- 8 empirical bounded-negative/topology-no-effect cases;
- 20 adversarial cases.

Total HARD = 28 cases.

All 36 cases are also reported separately.

## 5. LLM Repetitions

For B2/B3/B4:

- same exact model/version/configuration across conditions;
- 3 independent fresh runs per case per evidence condition;
- target: 36 × 3 × 3 = **324 reasoner evaluations**.

A provider/model-version change during scored evaluation invalidates the mixed run and requires a versioned restart unless a preregistered contingency applies.

## 6. Reasoner Input Isolation

LLM context must not contain:

- repository source/implementation;
- unit test names/results;
- hidden gold labels;
- expected experiment outcome;
- previous runs/conditions for the same case;
- prior project conversation revealing the answer;
- web/tools unless a future protocol version explicitly preregisters them.

Each call is fresh.

## 7. Common Structured Final Output

Every reasoner/baseline is projected into:

```text
classification:
  EFFECT_OBSERVED | BOUNDED_NEGATIVE | COUNTEREVIDENCE | INSUFFICIENT | AMBIGUOUS
abstain: bool
claims[]:
  claim_kind
  causal_strength
  evidence_refs[]
  text
unresolved[]
evidence_refs[]
```

Do not request or score hidden chain-of-thought.

## 8. Hidden Gold

Hidden gold is logically/physically separated from visible views.

Gold includes:

- classification;
- `must_abstain`;
- allowed evidence refs/support sets where applicable;
- invalid/wrong-scope evidence refs;
- counterevidence refs;
- maximum allowed causal strength;
- empirical vs derived-adversarial origin;
- source-run grouping to prevent pseudo-replication.

Projection code must not receive gold labels.

## 9. Empirical Capture

Frozen Phase-5E.4 machinery remains the mutation/evidence reference.

Evaluation capture may add a **read-only sidecar** to preserve RAW material from the same experiment interval. This is evaluation instrumentation, not a production collector feature.

The sidecar must:

- use preregistered bounded reads;
- avoid mutation;
- capture stable references/timestamps;
- record environment/boot scope;
- be checked for material observer effect.

## 10. Adversarial Transformations

Transformations are deterministic/versioned/frozen before scored LLM outputs.

Examples:

- remove samples to force insufficient coverage;
- insert wrong-boot otherwise-supportive evidence;
- add structurally unrelated temporally close anomaly;
- reverse relevant evidence ordering;
- introduce multiple plausible candidates without adequate isolation.

## 11. Condition Blinding

The model is not told “RAW”, “MINIMAL”, or “FULL.” The prompt calls input an evidence bundle.

Case IDs are opaque.

Prompt template/output schema are identical across LLM conditions except for the evidence block.

## 12. Parse / Retry Policy

- no human semantic repair of invalid model responses;
- invalid structured output counts according to scoring policy;
- transport/provider failures may retry the exact request under a preregistered retry limit;
- retries are logged.

## 13. Ablation

Mandatory:

- FULL vs MINIMAL;
- remove topology;
- remove coverage;
- remove boot/provenance;
- remove intervention metadata;
- remove counterevidence;
- remove exact timestamp basis;
- remove current derived synthesis/interpretation from FULL.

The last ablation detects whether FULL wins because it embeds answer-like current interpretation.

## 14. Analysis Sets

Report:

- full corpus (36);
- HARD subset (28; primary);
- empirical vs adversarial strata;
- per scenario family;
- per repeat/condition.

Do not treat adversarial variants from one source run as independent empirical replications.

## 15. Benchmark Validity Failures

The scored benchmark is invalid/requires a versioned restart if materially affected by:

- hidden-label leakage;
- gold generated circularly from synthesis being evaluated;
- mismatched underlying case across views;
- prompt/config differences across conditions;
- silent FULL truncation;
- model/version change;
- post-hoc hard-subset selection;
- scorer/threshold changes after results;
- human semantic repair;
- case metadata revealing outcome.

A specific invalidation is allowed; “more data needed” is not a substitute for a verdict when protocol remains valid.
