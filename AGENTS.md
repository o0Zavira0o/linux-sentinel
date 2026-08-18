# AI Engineering Instructions — Sentinel-X

This file defines mandatory operating rules for any AI coding agent or engineer before analyzing, modifying, reviewing, or extending Sentinel-X.

## 1. Bootstrap Rule

Before recommending or changing architecture or implementation:

1. read `docs/START_HERE.md`;
2. follow its mandatory reading order;
3. inspect the actual source relevant to the task;
4. inspect related phase documents and ADRs;
5. check `docs/KNOWN_EXCEPTIONS.md` and `docs/TECH_DEBT.md` before “cleaning up” anything unusual.

Do not reconstruct architectural intent from code alone.

## 2. Source of Truth Hierarchy

When sources appear inconsistent, use this hierarchy:

1. actual repository source for current implementation behavior;
2. accepted ADRs for architectural decisions and rationale;
3. `docs/CONSTRAINTS_AND_INVARIANTS.md` for non-negotiable properties;
4. `docs/CURRENT_STATE.md` for current implementation status;
5. `docs/ROADMAP.md` for future intent;
6. phase documents for phase-local history and constraints;
7. `KNOWN_EXCEPTIONS.md` and `TECH_DEBT.md` for intentional irregularities and deferred problems.

Never resolve contradictions silently. Report them.

## 3. Current Project Position

- Branch: `sentinel-x-phase1`
- Frozen implementation baseline: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Phase 0–4: frozen
- Phase 5A–5E.4: frozen reference implementation
- Active phase: **Phase 5F — Falsification**
- Phase 5F.0: **FROZEN — preregistration baseline established**
- Phase 5F.1: **FROZEN — blinded projection boundary established**
- Phase 5F.2: **FROZEN — deterministic baselines established**
- Phase 5F.3A: **CORRECTIVE CHECKPOINT FROZEN — the third live-discovered multiple-candidate hidden-gold/live-shape correction is frozen at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38` after Fedora validation, exact repository guards, push/remote equality, clean-repository verification, and green four-job CI**
- Phase 5F.3B: **FROZEN — authoritative attempt #4 captured 16 empirical cases, derived 20 adversarial cases, passed independent exported-artifact audit, and froze corpus-v1 with archive SHA-256 `a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`; no scored reasoner output exists**
- Phase 5F.4: **FROZEN — structured reasoner output boundary frozen at `3604fb3ed5571ee02f5a6e448628ae68bc201f73` after Fedora validation, exact repository guards, push/remote equality, clean-repository verification, and green four-job CI run `32059127141`; no scored reasoner output exists**
- Phase 5F.5: **FROZEN — the private M1–M8 primary-metrics scorer is frozen at implementation checkpoint `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5` after authoritative Fedora validation and exact-SHA CI run `32062765897`; the formal docs-only freeze/5F.6 activation commit `5d8c1e227894a88a1f46de715b506903ceb58045` also passed exact-SHA CI run `32105743211`; no scored reasoner output exists**
- Active milestone: **5F.6 — Ablation; the private provider-neutral generic execution preflight is FROZEN at implementation checkpoint `39d1db2845f6d461ab21a6da2077be2b77bf5982` after authoritative Fedora validation and exact-SHA four-job CI run `32108226447`. It pins prompt/schema identity, exact model/version/config manifest fields, one exact-request transport retry, stateless no-tool/no-web requests, deterministic 324-attempt ordering, and externally measured token-capacity checks. 5F.6 remains ACTIVE for the still-unfrozen concrete provider/model/tokenizer manifest, 108-request token-count map, exact 324-attempt plan, and mandatory-ablation mappings. No score-bearing request is authorized.**

The previous post-5E.4 feature-growth plan is superseded.

## 4. Binding Strategic Thesis

The active thesis is:

> **Sentinel-X exists to make operational diagnoses falsifiable.**

More precisely, Sentinel-X is being evaluated as an evidence-integrity and claim-bounding layer for operational reasoning.

Do not turn Sentinel-X into another generic monitoring agent, dependency mapper, chaos engine, generic RCA monolith, or LLM SRE agent.

## 5. Hard Freeze Until Phase 5F Decision

Do not add or expand:

- host observability features;
- fault modes;
- dependency relation support;
- synthesis hierarchies;
- identity families;
- protocol layers;
- remediation;
- RCA ranking;
- probability/confidence engines;
- distributed tracing collectors;
- storage backends;
- plugin systems;
- UI/dashboard;
- autonomous agents.

Do not refactor/delete the current Phase-5 FULL evidence implementation before Full-vs-Minimal ablation is complete.

## 6. Architecture Change Policy

Before changing any of these, inspect related ADRs and roadmap dependencies:

- module boundaries;
- persistence strategy;
- state ownership;
- public interfaces;
- event flow;
- dependency direction;
- privilege/authentication/authorization boundary;
- concurrency behavior;
- identity semantics;
- evidence scope or claim strength.

A frozen phase is reopened only for a confirmed regression, security defect, incorrect contract, architectural blocker, or new evidence invalidating an assumption.

## 7. Anomaly Handling

If code appears redundant, awkward, overengineered, or suspicious, do not immediately refactor it.

First classify it as one of:

- bug/regression;
- intentional architecture;
- compatibility behavior;
- known exception;
- technical debt;
- temporary workaround;
- frozen reference needed by the Phase-5F ablation;
- future-roadmap preparation.

## 8. Production/Research Engineering Principles

1. `systemd` is an execution mechanism; Sentinel-X is an evidence/intelligence layer.
2. Preserve Linux-native evidence before interpretation.
3. Prefer duplicate evidence to silent loss where at-least-once delivery applies.
4. Unknown remains explicit (`UNASSESSED` or equivalent).
5. Do not invent probability/confidence.
6. Bounded structures fail explicitly; no silent eviction unless explicitly designed.
7. Research claims never exceed evidence.
8. Missing evidence is not a negative finding.
9. Topology is not runtime effect.
10. Temporal consistency is not causality.
11. A controlled intervention is scoped evidence, not a universal causal law.

## 9. Phase-5F Rules

Phase 5F is a falsification phase, not a feature phase.

Before scored benchmark results are inspected:

- freeze thesis;
- freeze non-goals;
- freeze corpus rules;
- freeze baselines;
- freeze the corpus-v1 capture/derivation protocol before any scored reasoner output;
- freeze primary metrics;
- freeze decision thresholds;
- freeze kill criteria.

Representations:

- RAW — bounded raw operational evidence with stable references
- MINIMAL — minimum typed facts needed for evaluation
- FULL — current frozen Phase-5 evidence

MINIMAL is an evaluation projection, not a new public framework.

LLM runs must be blinded and isolated from repository context, hidden labels, expected experiment outcomes, and prior conversations revealing answers.

## 10. Test Strategy Rule

Green tests are a necessary merge condition, not proof of value.

Every new test must answer:

> What new failure class does this test protect?

Reject tests whose only justification is repeating the same primitive invalid-type/bounds invariant in another DTO.

See `docs/TESTING_STRATEGY.md`.

## 11. Fedora Validation Workflow

Fedora logs supplied by the operator are authoritative for runtime validation.

For meaningful code changes use:

```text
design
→ implementation
→ narrow static validation
→ dedicated tests
→ regression tests
→ full quality gate
→ live/smoke proof where applicable
→ repository/frozen guards
→ commit
→ push
→ CI
→ freeze
```

Stop at the first real failure and fix it before later steps.

### Interactive-shell safety

Do **not** use `exit 1` inside command blocks intended to be pasted into the operator's interactive shell. Guard blocks should use a shell function and `return 1`, then capture the return code and unset the function.

Do not provide `unzip` commands for project artifacts; the operator manually extracts/replaces files.

## 12. Documentation Update Matrix

Documentation is part of Definition of Done.

After **every meaningful push**, explicitly review/update:

| Change type | Documents to review/update |
|---|---|
| current phase/milestone/status | `docs/START_HERE.md`, `docs/CURRENT_STATE.md`, `docs/ROADMAP.md`, relevant phase file |
| completed phase/subphase | above + phase completion summary + ADRs if decisions changed |
| architecture/module/data-flow change | `docs/ARCHITECTURE.md`, relevant ADR, invariants if affected |
| strategic thesis/roadmap change | `docs/PROJECT_SPEC.md`, `docs/ROADMAP.md`, `START_HERE.md`, ADR |
| new/changed invariant | `docs/CONSTRAINTS_AND_INVARIANTS.md` + tests + ADR when architectural |
| intentional exception discovered | `docs/KNOWN_EXCEPTIONS.md` |
| real debt discovered | `docs/TECH_DEBT.md` |
| test philosophy/gate change | `docs/TESTING_STRATEGY.md` |
| public-facing status/capability change | `README.md` |
| Phase-5F preregistration before freeze | corresponding `research/phase5f/*.md` |
| benchmark result after preregistration | `CURRENT_STATE.md`, Phase-5 file, result report; do not rewrite criteria post hoc |

Do not duplicate authoritative content. Link to the authoritative document.

## 13. New Class/Module Budget

During Phase 5F, for every proposed class/module state:

```text
Unique invariant owned = ...
Why a function/simple structure is insufficient = ...
Number of real consumers = ...
```

If this cannot be justified, do not create it.

Architecture review is mandatory when a new subsystem starts approaching any of:

- ~700 LOC in one module;
- >20 public symbols;
- >5 identity types;
- multiple single-implementation Protocols;
- nested serialization wrapper hierarchies.

These are review triggers, not style targets.

## 14. Definition of Done

A task is complete only when:

- implementation/documentation scope is complete;
- appropriate validation passes;
- architecture and frozen contracts remain consistent;
- live proof is performed when real Linux behavior changed;
- documentation update matrix has been evaluated;
- roadmap assumptions are not violated;
- exact intended files are committed;
- local and remote heads match after push;
- repository is clean;
- required CI jobs are green;
- phase/milestone is frozen only after all relevant evidence is complete.
