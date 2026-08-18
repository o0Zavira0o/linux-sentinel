# Engineering Roadmap

## Roadmap Principles

1. maximize information gain before feature growth;
2. evidence value must be measured against simpler baselines;
3. current Phase-5 FULL architecture stays frozen until ablation;
4. delete/refactor based on measured value, not aesthetic preference;
5. reasoners remain replaceable; evidence contracts should not depend on one model/vendor;
6. integrate external telemetry/fault ecosystems rather than rebuilding them when broader scope is justified;
7. production hardening follows thesis validation, not vice versa.

## Current Position

- Phase 5E.4: FROZEN reference
- Phase 5F: ACTIVE
- Milestone: 5F.6 — Ablation; 5F.5 Primary Metrics is frozen at `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5` with green exact-SHA CI run `32062765897`, corpus-v1 and the 5F.4 structured-output boundary remain frozen, and no scored reasoner output exists

## Phase Overview

| Phase | Goal | Status | Architectural impact |
|---|---|---|---|
| 0 | Engineering baseline | FROZEN | typed/tested runtime/CI foundation |
| 1 | Linux host observability | FROZEN | native evidence collection foundation |
| 2 | systemd/journald evidence & correlation | FROZEN | service/journal evidence continuity |
| 3 | Fault Lab / controlled ground truth | FROZEN | real intervention/recovery methodology |
| 4 | detection/evaluation/incidents | FROZEN | conservative detection + empirical characterization |
| 5A–E.4 | dependency/propagation research reference | FROZEN | FULL evidence implementation retained for ablation |
| 5F | falsify/validate evidence thesis | ACTIVE | benchmark RAW/MINIMAL/FULL/heuristics/reasoners |
| 5R | evidence-based architecture reduction | CONDITIONAL | delete/internalize complexity only if thesis survives |
| 6 | evidence-constrained reasoning / Claim Gate | LOCKED | only after 5F PASS + 5R completion |
| 7 | generalization / topology–evidence divergence | LOCKED | external adapters/harder divergence research if justified |
| 8 | operationalization | LOCKED | privilege, durability, crash/soak/SLO/deployment |

## Phase 5F — Falsification

Official purpose:

> Determine whether Sentinel-X structured evidence materially improves operational reasoning over simpler alternatives.

### 5F.0 — Freeze & Pre-registration

Status: **FROZEN**. No scored benchmark result was authorized before this preregistration baseline.

Frozen before benchmark results:

- thesis;
- non-goals;
- evaluation protocol;
- baselines;
- metrics;
- corpus-generation rules;
- decision thresholds;
- kill criteria.

### 5F.1 — Evaluation Projection

Status: **FROZEN**.

Implemented a private evaluation-only boundary that:

- projects one gold-free case into RAW, MINIMAL, or FULL without exposing the condition label;
- uses opaque stable evidence references;
- restricts MINIMAL to preregistered factual categories;
- keeps hidden `CaseGold` physically/logically outside visible exports;
- leaves frozen `dependency/` behavior/public API unchanged.

### 5F.2 — Baselines

Status: **FROZEN**.

Implemented:

- B0 state/rule comparator;
- B1 simple requirement-graph + time-window comparator over MINIMAL factual evidence;
- B1S conservative projection of current frozen deterministic synthesis.

These implementations have not yet been scored; 5F.2 establishes comparators, not benchmark conclusions.

Later 5F milestones execute:

- B2 LLM RAW
- B3 LLM MINIMAL
- B4 LLM FULL
- optional human subset

### 5F.3 — Blind Falsification Corpus

Status: **FROZEN**.

**5F.3A — Corpus Contract & Capture Harness: THIRD CORRECTIVE CHECKPOINT FROZEN at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`.** The contract/harness baseline remains intact, but three live capture attempts exposed correctness defects before any corpus-v1 export or scoring: the post-fault sidecar boundary race, reverse derivation of live-shaped effect parents without a target timeline, and finally multiple-candidate hidden gold hardcoding optional parent ref `REF-0004`. The current corrective working set derives multiple-candidate supporting refs from actual child evidence and includes the target timeline only when present. Fedora validation passed, including the dedicated defect regressions, private Phase-5F regression set, full 1308-test project gate, and separate research-harness static validation. Exact repository/content guards, commit/push, local/remote equality, a clean repository, and the four-job CI matrix also passed; the corrective checkpoint is frozen and live capture may resume only by restarting the full campaign.

**5F.3B — Live Corpus Capture & Freeze: FROZEN.** Fresh attempt #4 ran from CI-proven repository checkpoint `64cffbe48c4a919752d68edae19f6a763295eb6e`, restarted the full campaign, captured all 16 empirical executions, derived all 20 adversarial cases, passed internal and independent exported-artifact audits, and froze corpus-v1 before any scored reasoner output. Corpus counts are 36 total / 16 empirical / 20 adversarial / 28 HARD. Frozen archive SHA-256: `a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`. No failed-attempt empirical objects were reused.

Corpus-v1 must include multiple cases of:

- clear observed effect;
- bounded negative evidence;
- insufficient coverage;
- reverse/counterevidence;
- topology without observed effect;
- temporal distractor;
- invalid provenance/cross-boot distractor;
- multiple-candidate ambiguity.

Empirical live cases must remain distinguishable from derived adversarial variants. Derived variants inherit the parent source-run group and are never counted as independent empirical replications. Transformations that invalidate the parent evidence assumptions must remove stale FULL derived records rather than rewriting them into answer-like evidence.

### 5F.4 — Structured Reasoner Output

Status: **FROZEN at `3604fb3ed5571ee02f5a6e448628ae68bc201f73`**.

The private strict bare-JSON parser/validator implements the preregistered output fields and claim shape, rejects structural/JSON ambiguity without semantic repair, and leaves abstention correctness, unsupported claims, citation quality, provenance validity, and counterevidence handling independently scoreable. Fedora validation, exact repository/stage guards, commit/push, local/remote equality, clean-repository verification, and four-job exact-SHA CI run `32059127141` all passed. No scoring, provider integration, prompt execution, or scored reasoner output was added by 5F.4.

### 5F.5 — Primary Metrics

Status: **FROZEN at `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`; exact-SHA CI run `32062765897` PASS**.

The private scorer implements the preregistered M1–M8 metrics, exact 36×3×3 scored-matrix validation, pseudoreplication grouping visibility, and all/HARD, empirical/adversarial, family, condition, and repeat slices. The frozen metric definitions, thresholds, HARD/full analysis sets, preregistration files, and benchmark-validity rules are not reopened. Authoritative Fedora validation passed 15/15 dedicated tests, the 89/89 private Phase-5F regression, and the full 1337/1337 repository gate with 156 Python files formatted and strict mypy clean across 84 source files. Exact repository/stage guards, checkpoint `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`, push/local-remote equality, clean-repository verification, and four-job exact-SHA CI run `32062765897` all passed. No scored reasoner output exists.

- task correctness;
- propagation Macro-F1;
- unsupported causal claim rate;
- correct abstention F1;
- evidence citation precision;
- provenance violation rate;
- counterevidence preservation rate;
- run-to-run consistency.

### 5F.6 — Ablation

Status: **ACTIVE**. Before the first scored output, freeze an execution manifest/preflight that proves identical B2/B3/B4 model/version, task wording, output schema, decoding/configuration, retry policy, fresh-context isolation, condition blinding, and adequate non-truncating context capacity. This operationalizes the frozen evaluation protocol; it does not reopen preregistered metrics, thresholds, corpus, or kill criteria.

Mandatory:

- FULL vs MINIMAL;
- remove topology;
- remove coverage;
- remove boot/provenance;
- remove intervention metadata;
- remove counterevidence;
- remove exact timestamps;
- remove current derived synthesis/interpretation from FULL.

### 5F.7 — Decision Gate

Required verdict:

- A — THESIS VALIDATED, FULL COMPLEXITY JUSTIFIED
- B — THESIS VALIDATED, MINIMAL EVIDENCE SUFFICIENT
- C — PARTIAL VALUE, NARROW THE THESIS
- D — NO MATERIAL EVIDENCE VALUE, PIVOT
- E — STOP DEPENDENCY/REASONING DIRECTION

No architecture reduction occurs before this verdict.

## Phase 5R — Architecture Reduction (Conditional)

Runs only if Phase 5F preserves meaningful evidence value.

Reduction targets are evidence-informed review triggers:

- shared evidence primitives;
- generic `ContentId` mechanism;
- <= ~10 externally meaningful identity concepts;
- dependency/research public API around <=20–25 stable symbols;
- shared primitive validators;
- Protocols mainly at real external/I/O boundaries;
- smaller synthesis operations;
- `ExperimentSpec` + `ExecutionRecord`-style protocol/execution core;
- rename evidence characterization if `calibration` remains non-probabilistic;
- later CLI split if still justified.

## Phase 6 — Evidence-Constrained Reasoning (Conditional)

Prerequisites:

1. Phase 5F thesis survives;
2. Phase 5R reduction complete;
3. surviving evidence primitives stabilized.

Planned capabilities:

- reasoner input view;
- `ClaimDraft` schema;
- reasoner-agnostic Claim Gate;
- Verified/Evidence-Carrying Diagnosis;
- deterministic heuristic reasoner;
- one external LLM adapter for evaluation, without vendor lock-in;
- claim audit trail.

Core rule: no reasoner emits unverified final diagnosis as accepted operational truth.

## Phase 7 — Generalization / Topology–Evidence Divergence

Only after Phase 6 value.

Priority questions:

1. declared dependency but no runtime effect;
2. runtime effect without declared dependency;
3. multi-hop propagation;
4. restart policy changing propagation;
5. hidden socket/file/network dependency;
6. simultaneous/competing causes;
7. partial observations;
8. delayed journal evidence;
9. topology changes during incident;
10. failure propagation differing from recovery propagation;
11. repeated intervention with non-stable effect.

Potential adapters if justified:

- OpenTelemetry evidence ingestion;
- standard metric/Prometheus ingestion;
- external chaos backend;
- multi-host scope;
- external benchmark adapters.

## Phase 8 — Operationalization

Only after demonstrated reasoning value/generalization:

- narrow privilege separation;
- explicit crash-durability policy;
- hard-crash/reboot campaigns;
- soak/load/high-event-rate validation;
- privacy/redaction;
- performance SLO;
- packaging/deployment;
- stable public API.

The project must not be called production-grade before these gates.

## Cross-Phase Dependencies

```text
5E.4 frozen FULL reference
        |
        v
5F falsification + ablation
        |
   +----+----+
   |         |
 FAIL       PASS
   |         |
 pivot/stop  v
            5R reduction
                |
                v
             Phase 6 Claim Gate
                |
                v
             Phase 7 generalization
                |
                v
             Phase 8 operationalization
```

## Decisions That Must Remain Reversible

Until Phase-5F result:

- Phase-5 public DTO/identity hierarchy;
- exact synthesis hierarchy;
- protocol/execution class boundaries;
- future reasoner vendor/model;
- external telemetry/fault backend choices.

## Decisions Expected to Remain Permanent Unless Invalidated

- evidence before interpretation;
- explicit missingness/unknown;
- no invented confidence;
- claims do not exceed evidence;
- controlled ground truth separated from detector output;
- at-least-once evidence preference where applicable;
- systemd as execution mechanism, not arbitrary-shell intelligence.

## Deferred Decisions

- production persistence architecture;
- stable external API;
- multi-host model;
- telemetry/tracing adapters;
- chaos backend integrations;
- remediation policy/executor;
- user-facing product/UI.
