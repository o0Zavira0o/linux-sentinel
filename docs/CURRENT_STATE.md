# Current Project State

## Current Phase

- Phase: **5F — Falsification**
- Status: **5F.3 FROZEN — corpus-v1 capture/audit/freeze complete; 5F.4 FROZEN — Structured Reasoner Output boundary CI-proven; 5F.5 FROZEN — Primary Metrics scorer and formal freeze transition CI-proven; 5F.6 ACTIVE — generic execution preflight FROZEN; deterministic-baseline common-scoring adapter FROZEN; local Ollama/Gemma Concrete Execution Freeze candidate Fedora/LIVE-VALIDATED with implementation checkpoint pending; exact 108 frozen-corpus token counts, concrete 324-attempt plan, mandatory-ablation mappings, deterministic corpus scores, and scored reasoner outputs remain absent**
- Last updated: 2026-08-18
- Last completed implementation milestone: Phase 5F.6 — Deterministic Baseline Common-Scoring Adapter (formally frozen)
- Last completed research-artifact milestone: Phase 5F.3B — Live Corpus Capture & Freeze
- Frozen Phase-5F.6 deterministic-baseline scoring adapter checkpoint: `6d86fff7d0699ca4d11d48cdd98538ed81eb0380`
- Phase-5F.6 deterministic-baseline adapter formal freeze commit: `4c453159f3f8cf8da29cf3603fd1211dbecc0fd6`
- Phase-5F.6 deterministic-baseline adapter formal freeze CI run: `32124532861`
- Phase-5F.6 deterministic-baseline scoring adapter CI run: `32123764282`
- Frozen Phase-5F.6 generic execution-preflight implementation checkpoint: `39d1db2845f6d461ab21a6da2077be2b77bf5982`
- Phase-5F.6 generic-preflight checkpoint CI run: `32108226447`
- Phase-5F.6 generic-preflight formal freeze commit: `291fa32cea930b78d933c3dd35e11260c98bab3f`
- Phase-5F.6 generic-preflight formal freeze CI run: `32108809270`
- Frozen Phase-5F.5 implementation checkpoint: `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`
- Phase-5F.5 checkpoint CI run: `32062765897`
- Phase-5F.5 formal freeze / Phase-5F.6 activation commit: `5d8c1e227894a88a1f46de715b506903ceb58045`
- Phase-5F.5 formal-freeze CI run: `32105743211`
- Frozen Phase-5F.4 implementation checkpoint: `3604fb3ed5571ee02f5a6e448628ae68bc201f73`
- Phase-5F.4 checkpoint CI run: `32059127141`
- Phase-5F.3B closure / Phase-5F.4 activation checkpoint: `5be2e1f8d14c6ed2d235a7b34473a857952fe111`
- Capture/freeze authority repository checkpoint: `64cffbe48c4a919752d68edae19f6a763295eb6e`
- Frozen Phase-5F.3A corrective implementation checkpoint: `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`
- Last verified repository checkpoint before 5F.3A: `295a57975253441dc4e2a18290ce5043a57e938a`
- Branch: `sentinel-x-phase1`

## Completed Capabilities

### Phase 0 — Engineering baseline
Typed package/runtime/config/EventBus/storage/CLI/quality/CI foundation.

### Phase 1 — Linux host observability
Host identity, CPU/load, memory, filesystem, disk I/O, network, and process observation/sampling.

### Phase 2 — systemd/journald evidence and correlation
Bounded service observation, journald reading/checkpoint continuity, exact/fallback correlation, live EventBus correlation bridge.

### Phase 3 — Fault Lab
Controlled Sentinel-owned systemd fixtures, two real fault modes, ground-truth windows, recovery verification, reproducible dataset artifacts.

### Phase 4 — Detection/evaluation/incidents
Conservative service detector, repeated controlled evaluation, incident lifecycle, live EventBus detection bridge, evidence characterization.

### Phase 5A–5E.4 — Dependency/propagation research reference
Dependency evidence/discovery/graph, temporal/coverage evidence, controlled pair experiments, live runner, synthesis, paired contrast, protocol provenance, protocol-bound live execution.

## Current Validation State

Last authoritative Fedora baseline for Phase 5E.4:

```text
140 Python files formatted
Ruff PASS
strict mypy: 74 source files PASS
unit tests: 1248 / 1248 PASS
GitHub Actions: 4 jobs green
repository clean after push
```

Canonical implementation baseline:

```text
1733485fdce630e4a3c32731c7dcbc62cbdebefb
phase5: add protocol-bound controlled live execution
```

Phase-5E.4 live proof observed:

```text
Requires -> affected_anomaly_observed (20 samples)
Wants    -> bounded_negative_evidence (20 samples)
paired profile -> requires_anomaly_wants_bounded_negative
same boot/backend binding verified
post-recovery verified
```

This is controlled/descriptive evidence, not a universal systemd causal claim.

Phase-5F.1 authoritative Fedora completion gate:

```text
145 Python files formatted
strict mypy: 78 source files
unit tests: 1265 total
```

Phase-5F.2 authoritative Fedora completion gate:

```text
147 Python files formatted
strict mypy: 79 source files
unit tests: 1285 total
```

The 5F.2 gate validates implementation integrity only; the deterministic baselines have not yet been scored on the frozen falsification corpus.

Phase-5F.3A adds corpus contracts/transformations plus an operator-only capture harness. The third corrective changeset passed Fedora validation: the two dedicated defect regressions, the 60 private Phase-5F tests, the full **1308/1308** unit-test gate, **152 source+test Python files** in format/lint scope, strict mypy over **82 source files**, and separate capture-harness static validation all passed. The corrective implementation checkpoint is frozen at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`.

Phase-5F.3B is now complete. The authoritative campaign ran from repository checkpoint `64cffbe48c4a919752d68edae19f6a763295eb6e`, whose transition CI run `32044606556` completed successfully across Python 3.11–3.14. Gate 1 preflight, Gate 2 live capture attempt #4, Gate 3 independent exported-artifact audit, and Gate 4 freeze/preservation all passed on boot `4ff955e4421e487b946423c40beb713c`. Corpus-v1 contains **36 cases: 16 empirical + 20 adversarial; HARD=28**. The frozen archive SHA-256 is `a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`; the preserved attempt-04 log SHA-256 is `5391a3014643c6bfc352c46e60b32b98ebb1a1985587531b641ff5c7913a1128`. The preservation set is operator-local, hash-verified, read-only, and outside the repository. No scored reasoner output exists.

The first Gate-3 operator audit attempt produced a false-positive failure because the audit command looked for `statistical_no_effect_claim` instead of the frozen serialized key `statistical_no-effect_claim`. A read-only root-cause check confirmed the corpus bytes were correct; the corrected full audit then passed without recapture, rewrite, or re-export.

`research/phase5f/CORPUS_V1_CAPTURE.md` is retained byte-for-byte as the pre-score execution protocol. Its historical top-line corrective-status wording is not rewritten after observing the successful live result; this document and the Phase-5 status document carry the authoritative post-capture completion state.

Phase-5F.4 is frozen at `3604fb3ed5571ee02f5a6e448628ae68bc201f73`. The private `_phase5f` boundary provides strict bare-JSON parsing plus exact validation of `classification`, independent `abstain`, `claims`, `unresolved`, and top-level `evidence_refs`; it preserves independently scoreable semantic failures rather than repairing them. Authoritative Fedora validation passed the 14 dedicated 5F.4 tests, the 74-test private Phase-5F regression, documentation integrity, and the full 1322/1322 unit-test gate with 154 Python files formatted, Ruff clean, and strict mypy clean across 83 source files. Exact repository/stage guards, commit/push, local/remote equality, clean-repository verification, and four-job CI run `32059127141` also passed. No provider integration, prompt runner, or scored reasoner output was added by 5F.4.

Phase-5F.5 is formally frozen. Its implementation checkpoint remains `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5` with exact-SHA CI run `32062765897`; the docs-only freeze/5F.6-activation transition is `5d8c1e227894a88a1f46de715b506903ceb58045`, whose exact-SHA CI run `32105743211` also passed across Python 3.11–3.14 with local/remote equality and a clean repository. No scored reasoner output existed at freeze.

Phase-5F.6 generic execution preflight is frozen at implementation checkpoint `39d1db2845f6d461ab21a6da2077be2b77bf5982` after authoritative Fedora validation, exact repository/stage guards, commit/push, local/remote equality, clean-repository verification, and exact-SHA four-job CI run `32108226447`. It adds one private function-only module and 16 dedicated tests; it imports neither hidden gold nor the frozen scorer and performs no provider call. It binds the frozen preregistration checkpoint, corpus archive, 5F.4 reasoner-output boundary, 5F.5 scorer, and 5E.4 FULL reference; fixes prompt-template SHA-256 `53924b77bb4b922fdebb484fa1022d56b782a902cb967034b1e0a4d8d5e2ab48` and output-schema SHA-256 `1874fe57f0efa0f03c65e9c0bfe2f022f824390d97e9a9a9e17bfdd463f4e788`; requires fresh stateless no-tool/no-web requests; pins provider/transport failure handling to at most one retry of the exact semantic request; and deterministically plans the 36 x 3 x 3 matrix only after all 108 full-request token counts prove adequate context capacity. Fedora validation passed 16/16 dedicated 5F.6 tests, 105/105 private Phase-5F tests, documentation integrity, and the full 1353/1353 unit-test gate with 158 Python files formatted, Ruff clean, and strict mypy clean across 85 source files. 5F.6 remains ACTIVE because the concrete provider/model/version/configuration/tokenizer manifest instance, tokenizer-measured 108-request capacity map, resulting exact 324-attempt plan, and corpus-specific mandatory-ablation mappings are still unfrozen. No score-bearing request or scored output exists.

The deterministic-baseline common-scoring adapter is frozen at implementation checkpoint `6d86fff7d0699ca4d11d48cdd98538ed81eb0380` after authoritative Fedora validation, exact stage/repository guards, commit/push, local/remote equality, clean-repository verification, and exact-SHA four-job CI run `32123764282`. Validation passed **12/12 dedicated adapter tests**, **27/27 frozen-scorer compatibility tests**, **117/117 private Phase-5F tests**, documentation integrity, and the full **1365/1365 unit-test gate** with **160 Python files** in format/lint scope, Ruff clean, and strict mypy clean across **86 source files**. The frozen adapter source/test identities are `e1f83b7683943bbf69d8c105f4292dd6a0fff08e67461b94ce0c607c2e4b6173` and `f169f76bafd937ede90a47c40d2e1bc3e7e61e8620e0b1636d79252b72e527f1`. The frozen scorer, metrics, thresholds, baselines, corpus, preregistration, and generic execution preflight remain unchanged. 5F.6 remains ACTIVE because concrete provider/model/tokenizer controls, the complete 108-request token-count map, exact 324-attempt plan, and mandatory ablation mappings are still unfrozen. No deterministic corpus score, provider call, score-bearing request, or scored reasoner output has been generated.

## Partially Completed / Research-Only Capabilities

- Phase-5 dependency/propagation subsystem is not wired into normal CLI runtime reasoning.
- Evidence bundles/protocol records exist but a general trusted public replay path is incomplete.
- Controlled experiment evidence is narrow (single-host systemd fixture domain).
- Current evidence characterization is not probability calibration.

## Not Yet Implemented

- scored comparison of B0/B1/B1S on the frozen corpus;
- external/blinded LLM evaluation process;
- reasoner output ClaimDraft runtime model;
- Claim Gate;
- Evidence-Carrying Diagnosis runtime artifact;
- topology–evidence divergence research corpus;
- cross-distro/systemd generalization;
- production privilege separation;
- production crash durability policy for all required state;
- production performance/SLO/soak validation;
- autonomous remediation.

## Current Architecture State

See `ARCHITECTURE.md`. Important fact: `src/sentinel_x/dependency/` is a frozen research subsystem and has no normal runtime consumer outside itself.

## Current API State

The package has large research symbol surfaces inherited from incremental development. Phase-5 dependency exports are intentionally retained until Full-vs-Minimal ablation can determine which machinery has measurable value.

Do not treat current public research exports as a commitment to future stable API.

## Current Integrations

- Linux native sources
- systemd/systemctl
- journald evidence path
- filesystem/JSONL persistence

No production LLM, OpenTelemetry, Prometheus, or external chaos integration exists.

## Current Blockers to Further Architecture Growth

1. structured evidence value vs RAW reasoning is unproven.
2. FULL Phase-5 complexity vs MINIMAL evidence is unproven.
3. current synthesis vs simple Graph+Time heuristic is unproven.
4. Phase-5F evaluation views/corpus must preserve blinding and the frozen preregistration.

## Current Temporary / Intentionally Retained Implementations

- Phase-5 identity/DTO/protocol hierarchy;
- 139 dependency exports;
- repeated validation logic in research models;
- root-level legacy baseline files;
- evidence characterization named `calibration` although it is not probabilistic calibration.

These are reduction candidates, not immediate refactor targets.

## Immediate Next Tasks

### 5F.0 — Freeze & Pre-registration
Status: **FROZEN**.

Frozen preregistration set:

- `research/phase5f/THESIS.md`
- `research/phase5f/NON_GOALS.md`
- `research/phase5f/EVALUATION_PROTOCOL.md`
- `research/phase5f/BASELINES.md`
- `research/phase5f/METRICS.md`
- `research/phase5f/KILL_CRITERIA.md`

No scored benchmark result is authorized to modify these documents post hoc.

### 5F.1 — Evaluation Projection & Blind Case Foundation
Status: **FROZEN**.

Implemented a private `_phase5f` evaluation boundary with one immutable gold-free `CaseSource`, identical-shape RAW/MINIMAL/FULL projection, opaque stable evidence references, preregistered MINIMAL factual categories, recursive hidden-gold leakage rejection, and separate non-exported `CaseGold`. Frozen `dependency/` behavior and public API remain unchanged.

### 5F.2 — Deterministic Baselines
Status: **FROZEN**.

Implemented three private function-only comparators on the 5F.1 boundary: B0 intentionally naive state/rule logic; B1 requirement-graph + monotonic-time + boot + bounded-coverage reasoning over MINIMAL factual evidence; and B1S conservative mapping of the existing frozen synthesis serialization. No benchmark score or value claim is assigned by this milestone.

### 5F.3 — Blind Falsification Corpus
Status: **FROZEN**.

**5F.3A — Corpus Contract & Capture Harness: THIRD CORRECTIVE CHECKPOINT FROZEN at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`.** The initial live attempt on commit `07e19d5dbb39f831a69e4b41b60624ec786558f8` exposed a capture-boundary race: the read-only sidecar could be stopped immediately after the frozen runner returned, before a completed sample round reached the recorded controlled-fault end. The validator correctly rejected that candidate case. The corrective harness keeps the sidecar running, with a bounded wait, until a completed round is timestamped at or after the closed fault end; it fails explicitly if this proof sample is not obtained. The failed attempt produced no valid corpus-v1 and no scored output.

**5F.3B — Live Corpus Capture & Freeze: FROZEN.** The second live attempt using corrective harness commit `8638bc033d6010c0d98aec67802cb24f75def864` exposed the reverse/counterevidence live-shape assumption. The third attempt on `7f405633d104bc1360a10d066dd75ebb8d027fbe` exposed the multiple-candidate hidden-gold assumption. Both failed before valid corpus freeze and produced no score. The third corrective implementation at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38` was then frozen, and the repository state was advanced to CI-proven capture authority `64cffbe48c4a919752d68edae19f6a763295eb6e`. Fresh attempt #4 restarted all empirical executions, captured 16 empirical cases, derived 20 adversarial cases, passed the frozen internal corpus audit and an independent exported-artifact audit, and froze the exact archive before any scoring. Frozen archive SHA-256: `a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`; preserved attempt log SHA-256: `5391a3014643c6bfc352c46e60b32b98ebb1a1985587531b641ff5c7913a1128`; capture boot: `4ff955e4421e487b946423c40beb713c`. `scored_reasoner_outputs_present=False`.

### 5F.4 — Structured Reasoner Output
Status: **FROZEN at `3604fb3ed5571ee02f5a6e448628ae68bc201f73`**.

The minimal private parser/validator implements the preregistered common machine-readable final-output boundary (`classification`, `abstain`, `claims`, `unresolved`, `evidence_refs`) without touching frozen baseline behavior or hidden gold. Validation intentionally preserves independently scoreable semantic failures: abstention is not derived from classification; uncited claims remain parse-valid; well-formed but wrong evidence references remain parse-valid for later citation/provenance scoring. Fedora validation, exact workset/stage guards, commit/push, local/remote equality, clean-repository verification, and exact-SHA four-job CI run `32059127141` all passed.

### 5F.5 — Primary Metrics
Status: **FROZEN at `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`; exact-SHA CI run `32062765897` PASS**.

The private scorer implements M1–M8 plus the preregistered all/HARD, empirical/adversarial, scenario-family, condition, and repeat aggregation boundaries without changing `research/phase5f/METRICS.md` or its thresholds. Hidden-gold scoring remains physically separate from visible projection. Authoritative Fedora validation passed 15/15 dedicated 5F.5 tests, the 89/89 private Phase-5F regression, documentation integrity, and the full 1337/1337 unit-test gate with 156 Python files formatted, Ruff clean, and strict mypy clean across 84 source files. Exact repository/stage guards, checkpoint `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`, push/local-remote equality, clean-repository verification, and four-job exact-SHA CI run `32062765897` all passed. No scored B2/B3/B4 output exists.

Pre-score implementation interpretations are pinned now, before any benchmark result exists: parse failure receives no semantic repair and fabricates no claims/citations; M1 counts it incorrect, M2 treats the missing class as a false negative for the gold class, and M4 treats missing abstention as no positive abstention prediction. M3 treats only `causal_strength != none` as a causal claim and orders the frozen strengths `none < association < hypothesis < established_cause`; a causal claim is unsupported only when that rank exceeds hidden gold. M3/M5/M6 retain zero natural denominators when no valid claims/references exist. M5/M6 count claim-level reference uses, not top-level references: M5 counts a use as valid only when the ref belongs to hidden `supporting_evidence_refs`, while M6 counts a violation only when the ref belongs to hidden `invalid_evidence_refs`. M7 succeeds only when preregistered counterevidence is explicitly referenced at claim/top level or the final classification exactly matches the hidden gold classification. M8 uses the three-run denominator and never treats parse failures as a modal classification. For secondary slices lacking a class entirely, per-class F1 remains missing and the slice macro mean uses only defined classes; the primary HARD/full sets contain all five preregistered classes.

### 5F.6 — Ablation
Status: **ACTIVE — generic execution preflight and deterministic-baseline common-scoring adapter FROZEN; local Ollama/Gemma Concrete Execution Freeze candidate Fedora/LIVE-VALIDATED with implementation checkpoint pending; no score-bearing request exists**.

The frozen generic preflight fixes the common task/prompt and output-schema digests, frozen-artifact identities, fresh stateless request shape, deterministic execution-order seed `sentinel-x.phase5f6-execution-order.v1`, and a pre-score transport policy of at most one retry of the exact request for provider/transport failure only. Invalid structured output is not a transport failure and remains unrepaired for the frozen scorer. The frozen generic boundary accepts an explicit concrete provider/model/version/configuration/tokenizer manifest rather than choosing one.

A pre-score completeness audit identified one implementation gap that had to close before concrete provider selection: the frozen 5F.5 `score_attempt`/summary row contract only accepts `raw`, `minimal`, or `full` as its condition field, while the frozen protocol separately requires the deterministic B1-vs-B1S HARD collapse comparison. Reopening or relabeling the frozen scorer would be inappropriate. The frozen corrective adapter keeps baseline identity (`B0`, `B1`, `B1S`) in an outer scorer-side record, passes each baseline's actual evidence projection through the unchanged frozen scorer (`minimal` for B0/B1; `full` for B1S), uses repeat index 1 only as an explicitly documented scorer-transport sentinel, and requires M8 to remain unavailable for deterministic single-run baselines. It validates exactly 108 baseline/case outputs and exposes the preregistered HARD B1/B1S summaries without computing a post-hoc verdict. No scored corpus output has been generated.

The deterministic adapter is formally frozen. A paid hosted-API candidate was abandoned before commit/provider request because a usable paid credential was unavailable; this is pre-score operational feasibility evidence, not a benchmark result. Concrete Execution Freeze now uses the local Ollama/Gemma path. Live identity evidence pins Ollama `0.32.13`, model digest `be1d79d105352d8cb0a25ee03f1f315935cc93fb4f0674422c2bb13be72fc025`, and GGUF SHA `93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b`. The service is loopback-only, cloud-disabled, single-parallel, and single-loaded-model. The Ollama model-store root remains mode 0700 under service account `ollama`; the regular operator cannot traverse it, while the service account can execute `llama-server`, read the pinned blob, and reproduce the exact GGUF SHA without permission broadening. The first live runtime-identity proof exposed a Python 3.14 `http.client` incompatibility because GET requests passed `headers=None`; the adapter now passes an empty mapping for bodyless GET requests and the existing runtime-identity test asserts GET `{}` versus POST JSON headers. Authoritative revalidation passed 18/18 dedicated tests, 34/34 execution+adapter tests, 135/135 private Phase-5F tests, Ruff/mypy, and the full 1383/1383 unit-test gate. A separate non-score-bearing equivalence proof rendered the frozen synthetic chat without model generation, tokenized that exact rendered template through loopback `llama-server /tokenize`, and reproduced the known `prompt_eval_count` exactly: 76 tokens from 401 rendered characters. No tokenizer process/listener remained afterward. The 8192-context synthetic capacity proof then completed normally and remained a Strong PASS at 87% GPU / 13% CPU, about 6633 MiB of 8151 MiB GPU memory, 24 GiB available system RAM, and zero swap. The candidate manifest still uses context 8192 / max output 2048 / safety 1024; these capacity controls remain pre-score candidates until all 108 frozen-corpus semantic requests are exactly counted and the generic no-truncation preflight passes. Future score-bearing payloads can be built but have no transport in this module. Exact 108 counts, the resulting 324-attempt plan, and mandatory ablation mappings must be frozen before scored inference.

No scored benchmark result should be generated or inspected until all of those pre-score controls are frozen. The six preregistration files, frozen corpus-v1, frozen 5F.4 output boundary, frozen 5F.5 scorer, metric meanings/thresholds, baselines, and FULL Phase-5E.4 reference remain unchanged.

## Frozen FULL Reference and Repository Checkpoints

- FULL Phase-5E.4 reference commit: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Frozen Phase-5F.2 repository checkpoint: `295a57975253441dc4e2a18290ce5043a57e938a`
- Frozen Phase-5F.3A corrective implementation checkpoint: `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`
- Phase-5F.3B capture/freeze authority checkpoint: `64cffbe48c4a919752d68edae19f6a763295eb6e`
- Phase-5F.3B closure / 5F.4 activation checkpoint: `5be2e1f8d14c6ed2d235a7b34473a857952fe111`
- Frozen Phase-5F.4 Structured Reasoner Output checkpoint: `3604fb3ed5571ee02f5a6e448628ae68bc201f73`
- Phase-5F.4 exact-SHA CI run: `32059127141`
- Frozen Phase-5F.5 Primary Metrics checkpoint: `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`
- Phase-5F.5 exact-SHA CI run: `32062765897`
- Phase-5F.5 formal freeze / 5F.6 activation commit: `5d8c1e227894a88a1f46de715b506903ceb58045`
- Phase-5F.5 formal-freeze exact-SHA CI run: `32105743211`
- Frozen corpus-v1 archive SHA-256: `a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`
- Branch: `sentinel-x-phase1`
- Date: 2026-08-17

The current repository HEAD must be read from Git. The FULL reference commit remains fixed for ablation even as private Phase-5F evaluation infrastructure advances.
