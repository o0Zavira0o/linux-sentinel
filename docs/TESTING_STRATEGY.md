# Testing Strategy

## Testing Philosophy

Sentinel-X uses tests to protect correctness contracts, but **test count is not evidence of product/research value**.

A green suite is a necessary merge/freeze condition. Empirical/live/comparative evidence is required for claims about real Linux behavior or reasoning value.

Every new test must answer:

> What new failure class does this test protect?

If the answer is “the same primitive invalid-type/bounds rule in another DTO,” the test should normally be rejected or moved to a shared primitive matrix.

## Test Layers

### Tier A — Primitive Unit Tests
Parser/validator/identity primitives.

### Tier B — Domain Behavior
State machines and epistemic rules: scheduler, incidents, journal continuity, coverage, future Claim Gate.

### Tier C — Component Integration
Multiple components composed in-process without real OS mutation.

### Tier D — Real Linux Integration
systemctl/journal/filesystem behavior on Fedora/Linux.

### Tier E — Live Controlled Experiment
Real mutation + observation + recovery.

### Tier F — Comparative Evaluation
Graph+Time vs synthesis; LLM RAW vs MINIMAL vs FULL; ablations; later operator subset.

**From Phase 5F onward, Tier F is more important than increasing Tier-A validation count.**

## Current Quality Gate

```bash
./scripts/check.sh
```

Runs:

1. compileall syntax validation
2. `ruff format --check`
3. `ruff check`
4. strict mypy package checking
5. unittest discovery under `tests/unit`

Frozen Phase-5E.4 FULL-reference baseline: 1248/1248 unit tests PASS.

Phase-5F.1 adds targeted projection/leakage tests. Its completion baseline is 1265 total unit tests with 145 Python files under format/lint scope and 78 source files under strict mypy.

Phase-5F.2 adds deterministic baseline semantics without Linux mutation: B0 intentionally ignores topology/provenance/coverage; B1 exercises boot-scoped requirement reachability, forward/reverse timing, ambiguity, distractors, and bounded healthy coverage; B1S validates and conservatively projects frozen synthesis serialization. Its completion baseline is 1285 total unit tests with 147 Python files under format/lint scope and 79 source files under strict mypy.

Phase-5F.3A adds private corpus/lineage/export contracts, empirical binding, five deterministic adversarial transformations, and an operator-only research capture harness. The initial 5F.3B attempt exposed a post-fault sampling race; the first correction added three dedicated harness tests. A second live attempt exposed a reverse-transform contract bug when empirical `EFFECT_OBSERVED` lacked a pairwise target timeline; the second correction added a live-shaped reverse regression. A third live attempt then progressed beyond reverse derivation and exposed the same optional-target-timeline assumption inside multiple-candidate hidden gold, where `REF-0004` was hardcoded despite being absent from valid live-shaped parents. The third correction makes multiple-candidate supporting refs evidence-derived and adds a live-shaped regression plus an equality check between B1 evidence refs and hidden supporting refs. The frozen 5F.3 baseline is 1308 unit tests. `research/phase5f/capture_corpus_v1.py` still requires separate compile/Ruff/direct-mypy validation because `scripts/check.sh` does not cover `research/`.

Phase-5F.4 adds one private reasoner-output parser/validator module plus 14 dedicated tests. Required semantics include bare JSON only; duplicate-key/non-standard-constant rejection; exact top-level and claim fields; five benchmark classifications; strict boolean abstention; the four causal-strength values already used by hidden gold; stable `REF-####` reference syntax; and no silent canonicalization/repair. Tests must also prove that classification and abstention remain independent, uncited claims stay parse-valid for UCCR/citation scoring, well-formed unknown references remain parse-valid for later provenance/citation scoring, hidden reasoning/condition extras are rejected, and frozen B0 output validates through the new common boundary. Authoritative Fedora validation confirms 1322/1322 unit tests, a 154-file Python format/lint scope, and strict mypy success across 83 source files. The dedicated 5F.4 suite passes 14/14 and the private Phase-5F regression passes 74/74; these are now checkpoint validation evidence rather than candidate expectations.

## Mandatory Validation by Change Type

### Documentation-only
- verify expected documentation files only;
- check UTF-8/readability/internal links;
- no source changes;
- run `./scripts/check.sh` before freeze to prove implementation baseline unchanged;
- exact stage guard + push + CI.

### Deterministic source logic
- narrow compile/Ruff/mypy;
- dedicated tests;
- relevant regression set;
- full gate.

### Linux reader/parser
Above + real Fedora smoke/integration proof when behavior changed.

### systemd/journal mutation/lifecycle
Above + controlled live proof with explicit mutation/cleanup/recovery.

### Phase-5F evaluation code
- tests for leakage, projection equivalence/scope, scoring, baseline semantics, corpus lineage, transform determinism, hidden/visible separation, source-run grouping, and atomic corpus export;
- no new public API unless separately approved;
- verify hidden labels never enter visible projection;
- preregistration remains frozen after scored-result inspection except versioned invalidation/result reporting.

## Regression Policy

Retain real regressions such as:

- systemctl string-array parser correction;
- correlation/detection recursion/backpressure interaction;
- fresh-unit load/prepare ordering;
- boot-binding TOCTOU before mutation;
- PID reuse handling;
- journal checkpoint/recovery continuity.

Do not delete regressions merely to reduce test count.

## Mocking/Fake Rules

- fakes are acceptable for deterministic unit tests;
- mocks/fakes do not substitute for Linux live proof;
- use injected callables/concrete collaborators before new Protocols solely for tests;
- never claim Linux behavior is validated only because mocks passed.

## Test Data Strategy

- deterministic fixtures for unit/domain behavior;
- Sentinel-owned lab fixtures for controlled ground truth;
- empirical and adversarial Phase-5F cases separated;
- hidden labels stored separately from visible evidence;
- derived adversarial variants are not independent live experiments.

## Known Testing Gaps

- no broad real reboot campaign;
- no cross-distro/systemd campaign;
- no long-running soak/high-rate benchmark;
- no complete hard-crash campaign;
- no production population accuracy estimate;
- no blinded LLM baseline yet;
- B0/B1/B1S are implemented but have not yet been scored on the frozen corpus;
- no operator-value study.

## Definition of Tested

Use the strongest justified qualifier only:

- unit-tested;
- integration-tested;
- Fedora live-tested;
- controlled-experiment validated;
- comparatively evaluated;
- externally generalized.
