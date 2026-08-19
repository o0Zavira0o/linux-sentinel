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

Phase-5F.5 adds one private scorer module plus 15 dedicated tests. The suite protects the score-bearing semantics that must be frozen before results: causal-strength ordering/UCCR, support-vs-invalid hidden-set membership for claim-level citation/provenance denominators, top-level-ref exclusion from M5/M6, explicit zero-denominator missingness, parse-failure treatment without fabricated semantics, counterevidence preservation, fixed classification correctness/Macro-F1 behavior, abstention F1, three-repeat consistency, exact 324-attempt RAW/MINIMAL/FULL matrix validation, scenario-family/HARD membership, and source-run-group pseudoreplication checks. Authoritative Fedora validation confirms **1337/1337 total unit tests**, **156 Python files** in format/lint scope, strict mypy success across **84 source files**, **15/15 dedicated 5F.5 tests**, and **89/89 private Phase-5F tests**. These are frozen checkpoint evidence. Exact repository/stage guards, checkpoint `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`, commit/push, local/remote equality, clean-repository verification, and four-job exact-SHA CI run `32062765897` all passed. No scored reasoner output exists; the next Tier-F validation target is the 5F.6 blinded execution preflight and comparative/ablation run.

Phase-5F.6 generic execution-preflight implementation adds one private provider-neutral module plus **16 dedicated tests**. Authoritative Fedora validation confirms **16/16 dedicated 5F.6 tests**, **105/105 private Phase-5F tests**, **1353/1353 total unit tests**, **158 Python files** in format/lint scope, and strict mypy success across **85 source files**. Exact repository/stage guards, checkpoint `39d1db2845f6d461ab21a6da2077be2b77bf5982`, commit/push, local/remote equality, clean-repository verification, four-job exact-SHA CI run `32108226447`, formal freeze `291fa32cea930b78d933c3dd35e11260c98bab3f`, and formal-freeze CI run `32108809270` all passed.

The deterministic-baseline scoring adapter adds one private scorer-side module plus **12 dedicated tests**. It proves that the frozen scorer SHA stays unchanged; B0/B1 use the frozen `minimal` evidence condition and B1S uses `full`; baseline identity remains external to the scorer row; repeat index 1 is only a deterministic scorer-transport sentinel; M8 remains unavailable; malformed outputs still fail through the common frozen reasoner-output validator; exactly 108 B0/B1/B1S-by-case records are required; scenario-family/HARD/source-run-group lineage remains frozen; and the HARD B1/B1S comparison inputs are exposed without changing any metric formula or threshold. Authoritative Fedora validation confirms **12/12 dedicated adapter tests**, **27/27 frozen-scorer compatibility tests**, **117/117 private Phase-5F tests**, **1365/1365 total unit tests**, **160 Python files** in format/lint scope, and strict mypy success across **86 source files**. Exact stage/repository guards, checkpoint `6d86fff7d0699ca4d11d48cdd98538ed81eb0380`, commit/push, local/remote equality, clean-repository verification, and four-job exact-SHA CI run `32123764282` all passed. The deterministic-baseline common-scoring adapter is frozen; no score-bearing request exists.

The local Phase-5F.6 Ollama/Gemma execution-controls path retains one private provider adapter plus **18 dedicated tests**. The first checkpoint `991fe365300ed6a507d0e961fda48ed4bad8e566` / CI `32155042040` pinned runtime/model/GGUF identity, isolation, renderer/parser/template, budgets, repeat seeds, sampling, no tools/web/thinking/truncation/context shifting/schema repair, pure future score-payload construction, debug-render-only/tokenizer semantics, bounded HTTP, and no normal score-bearing transport. Pre-score token evidence falsified context 8192 at 56/108 over budget; synthetic long-context proof justified context 49152. The repo-only correction passed **18/18 dedicated**, **34/34 execution+adapter**, **135/135 private Phase-5F**, documentation integrity, **1383/1383 total unit tests**, **162 Python files** in format/lint scope, strict mypy across **87 source files**, exact stage/repository guards, checkpoint `29573586877ebcbc475ed2f00172129089e60fdd`, push/local-remote equality, and exact-SHA four-job CI `32169631419`. The corrected manifest is `e0da42efc5e67fc3d86b434a99e3e46dda257ba1a8b049c2adbb408e79582ee6`. The authoritative clean blinded rebind prohibits `ZipFile.testzip()`, reads only RAW/MINIMAL/FULL member content, reproduces count-map `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2`, records zero hidden-member content reads, and proves 0/108 corrected capacity violations. The exact deterministic 324-attempt plan `0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542` / file `61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80` passed independent plan-SHA recomputation, double-build equality, unique ID/distribution checks, deterministic order reconstruction, repeat semantic identity, capacity checks, and zero hidden/provider/scoring activity. Binding candidate `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3` checksum-binds the concrete-execution prerequisites. The mandatory-ablation implementation checkpoint `ea137456c97266c3c853a546e5af288b77008867` adds one private module plus 20 dedicated tests; authoritative Fedora validation passed **20/20 dedicated ablation tests**, **155/155 private Phase-5F tests**, **1403/1403 total unit tests**, **164 Python files** in format/lint scope, and strict mypy across **88 source files**. Exact stage/repository guards, push equality, and exact-SHA four-job CI run `32228043568` all passed.
The mandatory-ablation scorer-side adapter checkpoint `07330c4d68d149075ce0c906e4f50e046d9221b1` adds one private adapter module plus **21 dedicated tests**. It pins execution semantics at `d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d`, leaves frozen scorer SHA `8ef2514805bc3c7b788a7f13f24e8183c8a3ddb3c472015797e8721368b3b22e` and mapping SHA `9cfdda96a2228148df030549a0528c13f02108835cd5595eb5b5040d82083b43` unchanged, requires the exact **756-output** matrix, preserves mapping identity outside scorer rows, and protects independent per-mapping M8 grouping. Authoritative Fedora validation passed **21/21 dedicated adapter tests**, **176/176 private Phase-5F tests**, **1424/1424 total unit tests**, **166 Python files** in format/lint scope, and strict mypy across **89 source files**. Exact content/stage/repository guards, commit `07330c4d68d149075ce0c906e4f50e046d9221b1`, push/local-remote equality, clean-repository verification, and exact-SHA four-job CI run `32262977658` all passed.
Mandatory ablation mappings are now evidence-complete before scoring: the seven field-level mappings are preregistered at SHA-256 `5a4a7188742788954c2d7b1d11085057ab56594327c8306305286d9ba1b41844` (receipt `07df5842df8d3f9e9f4f49156068daa15e8689b28895547b39429a69e4493054`); the private deletion-only implementation is CI-proven at `ea137456c97266c3c853a546e5af288b77008867` / exact-SHA CI run `32228043568`; the 252-coordinate visible-only realization is frozen at `2d6afb218e01f14e0e3ac46711a60a1816dad0688d0365b3bc24c4865100f490` (summary `e1764bcb93042f1ad7ff7a3f98306c679a99d8c52c8f1a4455d4d7581a606165`, receipt `81a8b03fdd59003a118d2d1016f293fd8856ea1571876050c980c85618fe3205`); and the corrected 252-request render/token capacity audit is frozen at count-map `e5289d08fc2387e9fc1ad5a6c47c663f79ed4ef5387cbd8f43abf3cb8b2937cb`, evidence `7e7d358b24a9f386b27e981d51f958f5b72d05d06eb74089a6210d91a4f48cd6`, summary `803ca78c23702baced9261b86ccf41124185faf65cbd9686ad64812d1af6e2b2`, and receipt `921552ec7a711d583b8098a41cf482b771936915ca3f2ad698d47192ef674531`.
All 252 ablation prompts fit the frozen 49152 context / 2048 output / 1024 safety contract: maximum measured input is 11369 tokens against the 46080-token limit, with 0/252 over budget. The 32 unchanged counterevidence views match their frozen MINIMAL token counts exactly. The first token-capacity attempt is preserved as a pre-count harness canonicalization failure (failure SHA-256 `7cee6464f22ba4c33b7cf95276e9a490837bf2a87aecedca46af857c6ce4bf4f`); it was not a capacity falsification.
The Formal Mandatory Ablation Mapping Freeze is closed at repository checkpoint `114a397eaf341cd044804220ed00a84fb0c8c70e`; `mandatory_ablation_mappings_frozen=True`. The private scorer-side mandatory-ablation adapter is CI-proven at `07330c4d68d149075ce0c906e4f50e046d9221b1` / exact-SHA CI run `32262977658`. It leaves frozen `scoring.py` and `ablation.py` unchanged, pins scorer-side execution semantics at SHA-256 `d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d`, requires exactly 756 already-captured outputs (7 mappings × 36 cases × 3 repeats), routes six mappings through frozen `minimal` and one through `full`, keeps mapping identity outside frozen scorer rows, and computes M8 independently per mapping. Authoritative Fedora validation passed 21/21 dedicated adapter tests, 176/176 private Phase-5F tests, and 1424/1424 full unit tests with 166 Python files in format/lint scope and strict mypy across 89 source files. This documentation-only changeset is the Formal Ablation Scoring Adapter Freeze transition and becomes authoritative only after its own green exact-SHA CI; `ablation_scoring_adapter_frozen=True` only at that boundary. `scored_execution_authorized=False` remains mandatory. Before any benchmark score-bearing request, the exact external ablation execution semantics (request matrix/repeats/order/seeds, retry/output-capture behavior, and external execution identity) must be frozen separately without reopening the mappings, scorer, scoring adapter, thresholds, corpus, or FULL reference.
For this docs-only Formal Ablation Scoring Adapter Freeze transition, require exact documentation workset, UTF-8/link/whitespace checks, unchanged source/frozen-boundary hashes, full `./scripts/check.sh`, exact stage guard, push equality, and exact-SHA four-job CI.

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
