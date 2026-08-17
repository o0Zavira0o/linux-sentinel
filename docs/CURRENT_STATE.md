# Current Project State

## Current Phase

- Phase: **5F — Falsification**
- Status: **5F.3A third corrective checkpoint FROZEN at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`; 5F.3B ACTIVE — full live corpus capture restarts from the beginning**
- Last updated: 2026-08-17
- Last completed implementation milestone: Phase 5F.3A — Corpus Contract & Capture Harness
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

Phase-5F.3A adds corpus contracts/transformations plus an operator-only capture harness. The third corrective changeset has now passed Fedora validation: the two dedicated defect regressions pass, the 60 private Phase-5F tests pass, the full engineering gate passes with **1308/1308 unit tests**, **152 source+test Python files** in format/lint scope, and strict mypy over **82 source files**; `research/phase5f/capture_corpus_v1.py` also passes separate compile/Ruff/direct-mypy validation. The corrective checkpoint is re-frozen at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`: Fedora validation passed, the exact 10-file repository/content guards passed, local/remote equality and a clean repository were proven after push, and the four-job GitHub Actions quality matrix completed successfully. 5F.3A itself produces no corpus and no score; 5F.3B now performs the real live campaign from the beginning.

## Partially Completed / Research-Only Capabilities

- Phase-5 dependency/propagation subsystem is not wired into normal CLI runtime reasoning.
- Evidence bundles/protocol records exist but a general trusted public replay path is incomplete.
- Controlled experiment evidence is narrow (single-host systemd fixture domain).
- Current evidence characterization is not probability calibration.

## Not Yet Implemented

- Phase-5F blind empirical/adversarial corpus capture and lineage validation;
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
Status: **ACTIVE**.

**5F.3A — Corpus Contract & Capture Harness: THIRD CORRECTIVE CHECKPOINT FROZEN at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`.** The initial live attempt on commit `07e19d5dbb39f831a69e4b41b60624ec786558f8` exposed a capture-boundary race: the read-only sidecar could be stopped immediately after the frozen runner returned, before a completed sample round reached the recorded controlled-fault end. The validator correctly rejected that candidate case. The corrective harness keeps the sidecar running, with a bounded wait, until a completed round is timestamped at or after the closed fault end; it fails explicitly if this proof sample is not obtained. The failed attempt produced no valid corpus-v1 and no scored output.

**5F.3B — Live Corpus Capture & Freeze: ACTIVE.** The second live attempt using corrective harness commit `8638bc033d6010c0d98aec67802cb24f75def864` exposed and led to correction of the reverse/counterevidence live-shape assumption. The third attempt on `7f405633d104bc1360a10d066dd75ebb8d027fbe` progressed through empirical acquisition and reverse derivation, then failed in multiple-candidate ambiguity construction because hidden gold still hardcoded optional parent ref `REF-0004`. Production empirical effect cases may lack that target timeline. The `CorpusCase` invariant correctly rejected the child because hidden gold referenced evidence absent from the visible case. No corpus-v1 was exported and no scored output exists. The multiple-candidate correction derives its supporting refs from the actual child evidence and includes the target timeline only when present, then 5F.3B restarts from the beginning.

## Frozen FULL Reference and Repository Checkpoints

- FULL Phase-5E.4 reference commit: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Frozen Phase-5F.2 repository checkpoint: `295a57975253441dc4e2a18290ce5043a57e938a`
- Branch: `sentinel-x-phase1`
- Date: 2026-08-16

The current repository HEAD must be read from Git. The FULL reference commit remains fixed for ablation even as private Phase-5F evaluation infrastructure advances.
