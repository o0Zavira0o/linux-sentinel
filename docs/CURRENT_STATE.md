# Current Project State

## Current Phase

- Phase: **5F — Falsification**
- Status: **5F.3 FROZEN — corpus-v1 capture/audit/freeze complete; 5F.4 ACTIVE — Structured Reasoner Output implementation candidate Fedora-validated, repository checkpoint/CI freeze pending**
- Last updated: 2026-08-17
- Last completed implementation milestone: Phase 5F.3A — Corpus Contract & Capture Harness
- Last completed research-artifact milestone: Phase 5F.3B — Live Corpus Capture & Freeze
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

Phase-5F.4 now has a minimal implementation candidate in the private `_phase5f` boundary: strict bare-JSON parsing plus exact validation of `classification`, independent `abstain`, `claims`, `unresolved`, and top-level `evidence_refs`. Claim validation covers `claim_kind`, the frozen causal-strength vocabulary, claim-local evidence references, and claim text. The parser rejects duplicate JSON keys, non-standard JSON constants, prose/code fences, schema extras, malformed evidence references, and duplicate references without performing semantic repair. It deliberately does **not** infer abstention from classification, require evidence on every claim, or resolve reference validity against hidden gold, because those failures must remain observable to the preregistered metrics. No scorer, provider integration, prompt runner, or scored reasoner output has been added. Authoritative Fedora validation is complete: the 14 dedicated 5F.4 tests and 74-test private Phase-5F regression pass, documentation integrity passes, and the full quality gate reports 154 Python files already formatted, Ruff clean, strict mypy clean across 83 source files, and 1322/1322 unit tests passing. The implementation checkpoint is not frozen until repository/stage guards, commit/push, and exact-SHA green CI complete.

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
Status: **ACTIVE — IMPLEMENTATION CANDIDATE UNDER VALIDATION**.

A minimal private parser/validator candidate now implements the preregistered common machine-readable final-output boundary (`classification`, `abstain`, `claims`, `unresolved`, `evidence_refs`) without touching frozen baseline behavior or hidden gold. Validation intentionally preserves independently scoreable semantic failures: abstention is not derived from classification; uncited claims remain parse-valid; well-formed but wrong evidence references remain parse-valid for later citation/provenance scoring. Next work is Fedora validation, exact workset guards, commit/push/CI, and freeze of this boundary. Do not run scored B2/B3/B4 evaluations until that freeze completes.

## Frozen FULL Reference and Repository Checkpoints

- FULL Phase-5E.4 reference commit: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Frozen Phase-5F.2 repository checkpoint: `295a57975253441dc4e2a18290ce5043a57e938a`
- Frozen Phase-5F.3A corrective implementation checkpoint: `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`
- Phase-5F.3B capture/freeze authority checkpoint: `64cffbe48c4a919752d68edae19f6a763295eb6e`
- Phase-5F.3B closure / 5F.4 activation checkpoint: `5be2e1f8d14c6ed2d235a7b34473a857952fe111`
- Frozen corpus-v1 archive SHA-256: `a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`
- Branch: `sentinel-x-phase1`
- Date: 2026-08-17

The current repository HEAD must be read from Git. The FULL reference commit remains fixed for ablation even as private Phase-5F evaluation infrastructure advances.
