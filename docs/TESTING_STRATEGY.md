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

Phase-5F.3A adds private corpus/lineage/export contracts, empirical binding, five deterministic adversarial transformations, and an operator-only research capture harness. Expected completion baseline is 1303 total unit tests with 151 Python files under the source/test format/lint scope and 82 source files under strict package mypy. Because `scripts/check.sh` does not include `research/`, `research/phase5f/capture_corpus_v1.py` requires separate compile and Ruff checks before freeze. No live proof is needed to commit 5F.3A; the entire purpose of 5F.3B is to execute the frozen harness and produce the real corpus.

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
