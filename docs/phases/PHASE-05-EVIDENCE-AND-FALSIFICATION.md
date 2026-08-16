# Phase 05 — Dependency Evidence, Propagation Research, and Falsification

## Status

- 5A–5E.4: **FROZEN reference**
- 5F: **ACTIVE**
- 5R: conditional

Frozen implementation baseline:
`1733485fdce630e4a3c32731c7dcbc62cbdebefb`

## Why Phase 5 Initially Existed
Move from “what service is broken?” toward dependency-aware evidence about possible downstream effects without silently upgrading topology/correlation into causality.

## 5A — Typed systemd Dependency Evidence
Status: FROZEN

Relations:
- `Requires`
- `Wants`
- `After`
- `Before`

Requirement and ordering remain distinct; evidence is explicitly noncausal.

Commit: `190b9c82b2b84e159034ea868c353b110b605647`.

## 5B — Bounded Dependency Discovery
Status: FROZEN after parser correction

Traversal follows requirement relations and retains ordering as context.

Original: `1644eb073d47b415406542c9885593942f28d14d`
Corrective parser/evidence-ingestion: `ecf384bbabf78df93c6960d42207fc17795049c0`

## 5C — Versioned Dependency Graph
Status: FROZEN

Separates topology identity from graph/evidence version.

Commit: `1b75891ceded163fe3763cfa68f93a8335ebe084`

## 5D.1 — Temporal Propagation Evidence
Status: FROZEN after temporal-basis correction

Distinguishes state-change timing, assessment-time fallback, forward/outside/simultaneous/reverse/limited findings, and counterevidence vs insufficient timing.

Commit: `4c97dd95354d442670dadef715c7e528806ad27d`

## 5D.2 — Sampling Coverage / Negative Evidence
Status: FROZEN

Core rule:

> no anomaly observed under sparse/no sampling is not “no propagation.”

Commit: `c140fde3ed69444054ec9d85036ec15fbb131167`

## 5D.3A — Controlled Pair Experiment Contracts
Status: FROZEN

Deterministic source/dependent fixtures, Requires/Wants contrast, fixed `After=source`, intervention ground truth, conservative evidence classes.

Commit: `7475bdbaebc28685effd4afec5907b57f572a319`

## 5D.3B — Controlled Live Runner
Status: FROZEN

Actual manager verification, baseline health, concurrent dependent sampling, source fault, recovery, coverage, experiment evidence.

Commit: `71f7c421937fc580b28646be0d26c7438e94aa93`

Historical canonical pattern:
- Requires -> affected anomaly observed
- Wants -> bounded negative evidence

Not a universal causal/systemd claim.

## 5E.1 — Candidate-Local Conservative Evidence Synthesis
Status: FROZEN

Commit: `ba75f63253d8e7be21c43105148b3ce7b27e8c9f`

## 5E.2 — Typed Controlled Paired Contrast
Status: FROZEN

Commit: `67306b47c62dcf9bc91ad006b8e42b857a9f7e00`

## 5E.3 — Typed Pre-Execution Protocol Provenance
Status: FROZEN

Commit: `8dea56a4a99956246234b1a2cd91cddc8040c8ca`

## 5E.4 — Protocol-Bound Controlled Live Execution
Status: FROZEN

Commit: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`

Binds intended protocol, boot stability, explicit Sentinel-X backend profile, execution attempt, actual live runner, and exact result binding.

An audit closed a boot-binding TOCTOU before live mutation.

Canonical live proof:

```text
boot = 4ff955e4421e487b946423c40beb713c
backend_profile = backendprof-89422c6008ed5c7636d058ea2c672fb34d0645189ebe3c1270056eea529754b9
Requires evidence = affected_anomaly_observed, samples=20
Wants evidence = bounded_negative_evidence, samples=20
paired profile = requires_anomaly_wants_bounded_negative
canonical JSON SHA-256 = 295e8bc66b65ad8f56ad5d2a2ab74fcf1c5ab37e7c8e45d07340ce0b06b002b2
```

## Strategic Reset

Adversarial audit verdict:

```text
INTERESTING INFRASTRUCTURE, UNPROVEN VALUE
GOOD THESIS CANDIDATE, OVER-ENGINEERED IMPLEMENTATION
```

Binding decision:

> Stop Phase-5 feature growth. Keep FULL reference intact. Attempt to falsify the thesis before more architecture.

New thesis:

> **Sentinel-X exists to make operational diagnoses falsifiable.**

## 5F — Falsification (ACTIVE)

Official objective:

> Falsify or validate the claim that Sentinel-X structured evidence materially improves operational reasoning over simpler alternatives.

### 5F.0 — Freeze & Pre-registration
Status: **FROZEN**.

Required:
- `research/phase5f/THESIS.md`
- `NON_GOALS.md`
- `EVALUATION_PROTOCOL.md`
- `BASELINES.md`
- `METRICS.md`
- `KILL_CRITERIA.md`

No scored benchmark result is inspected before freeze. The preregistration documents are now the fixed benchmark contract unless the benchmark itself is invalidated and restarted under a new version.

Documentation note: the six preregistration files were substantively frozen at `f47b5cb764030ea8c78d53ff76ebe0859bb4ca29`. During 5F.1 their stale `PRE-REGISTERED DRAFT` header was corrected to `FROZEN`; no thesis, baseline, corpus, metric, threshold, or kill criterion content changed.

### 5F.1 — Evaluation Projection
Status: **FROZEN**.

Private `_phase5f` boundary now produces RAW / MINIMAL / FULL views from one immutable gold-free `CaseSource`, preserves opaque evidence references, restricts MINIMAL to preregistered factual categories, and keeps hidden `CaseGold` out of package-root visible exports. No frozen Phase-5 evidence model or public dependency API changed.

### 5F.2 — Baselines
Status: **FROZEN**.

Implemented function-only private comparators without changing frozen `dependency/`: B0 maps dependent service state while intentionally ignoring topology/timing/provenance/coverage; B1 uses MINIMAL factual scope, requirement reachability, monotonic source/target transitions, boot compatibility, ambiguity, and bounded healthy coverage; B1S validates current synthesis schema/count/interpretation claim boundaries and maps it conservatively to common benchmark labels. Forward temporal consistency alone remains `INSUFFICIENT`, not an observed effect. No scored comparison or value claim is assigned by 5F.2.

### 5F.3 — Blind Corpus
Status: **CURRENT MILESTONE**.

Build empirical live cases plus separated adversarial cases covering positive, bounded negative, insufficient, counterevidence, topology/no-effect, temporal distractor, invalid provenance, and multi-candidate ambiguity. Same-case RAW/MINIMAL/FULL lineage, source-run grouping, and deterministic adversarial transformation provenance must be frozen before scored outputs.

### 5F.4–5F.6
Structured final outputs, preregistered scoring, Full-vs-Minimal and field ablations.

### 5F.7 — Decision
Must choose A/B/C/D/E verdict.

## 5R — Conditional Reduction
Only if the evidence thesis survives.

Reduction targets:
- shared evidence primitives;
- identity collapse;
- validator collapse;
- public API collapse;
- Protocol reduction;
- smaller synthesis operations;
- `ExperimentSpec` + `ExecutionRecord`-style core;
- calibration naming correction.

## Must Not Be Refactored Yet
Until 5F decision:
- current dependency models/protocol/synthesis hierarchy;
- FULL evidence fields;
- identity names used by FULL;
- live experiment semantics needed to reconstruct reference cases.

## Phase Exit Criteria
Phase 5 ends only after Phase-5F verdict. If thesis survives, 5R completes before Phase 6. If thesis fails, pivot/stop occurs before Claim Gate implementation.
