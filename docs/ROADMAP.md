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
- Milestone: 5F.1 — Evaluation Projection & Blind Case Foundation

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

Status: **ACTIVE — current milestone**.

Create evaluation-only views of identical cases:

- RAW
- MINIMAL
- FULL

MINIMAL is not a new public framework.

### 5F.2 — Baselines

- B0 state/rule
- B1 simple Graph+Time
- B1S current deterministic synthesis projection for architecture comparison
- B2 LLM RAW
- B3 LLM MINIMAL
- B4 LLM FULL
- optional human subset

### 5F.3 — Blind Falsification Corpus

Must include multiple cases of:

- clear observed effect;
- bounded negative evidence;
- insufficient coverage;
- reverse/counterevidence;
- topology without observed effect;
- temporal distractor;
- invalid provenance/cross-boot distractor;
- multiple-candidate ambiguity.

Empirical live cases must remain distinguishable from derived adversarial variants.

### 5F.4 — Structured Reasoner Output

Score only final machine-readable output; do not request/score hidden chain-of-thought.

### 5F.5 — Primary Metrics

- task correctness;
- propagation Macro-F1;
- unsupported causal claim rate;
- correct abstention F1;
- evidence citation precision;
- provenance violation rate;
- counterevidence preservation rate;
- run-to-run consistency.

### 5F.6 — Ablation

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
