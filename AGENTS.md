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
- Active milestone: **5F.6 — Ablation; generic execution preflight and deterministic-baseline common-scoring adapter are FROZEN. The corrective local Ollama/Gemma context implementation is CI-PROVEN at `29573586877ebcbc475ed2f00172129089e60fdd` / run `32169631419` and pins context 49152, max output 2048, safety 1024, unchanged model/runtime/sampling identity, and no normal score-bearing transport. The authoritative blinded 108-count rebind reads member content only from RAW/MINIMAL/FULL, never calls `ZipFile.testzip()`, records zero hidden-member content reads, reproduces token-count SHA-256 `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2`, and yields 0/108 capacity violations under the 46080-token input budget while independently reproducing the legacy 8192 falsification at 56/108 over budget. The exact deterministic 324-attempt plan is frozen at semantic SHA-256 `0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542` / file SHA-256 `61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80` with 324 unique attempt IDs, exact 108/108/108 condition and repeat distributions, verified execution order, repeat semantic identity, and zero hidden/provider/scoring activity. Concrete-execution evidence is cryptographically bound by candidate binding SHA-256 `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3` / receipt SHA-256 `729367c756e8b7ac35c10ccfd50ac10fd0846face9e2bc1547813077ccf0a806`. Earlier token-count harnesses that called `ZipFile.testzip()` remain preserved as procedural history: their numeric count map was reproduced exactly, but they are not authoritative for the hidden-member-content-read claim. Mandatory ablation mappings are now evidence-complete before scoring: the seven field-level mappings are preregistered at SHA-256 `5a4a7188742788954c2d7b1d11085057ab56594327c8306305286d9ba1b41844` (receipt `07df5842df8d3f9e9f4f49156068daa15e8689b28895547b39429a69e4493054`); the private deletion-only implementation is CI-proven at `ea137456c97266c3c853a546e5af288b77008867` / exact-SHA CI run `32228043568`; the 252-coordinate visible-only realization is frozen at `2d6afb218e01f14e0e3ac46711a60a1816dad0688d0365b3bc24c4865100f490` (summary `e1764bcb93042f1ad7ff7a3f98306c679a99d8c52c8f1a4455d4d7581a606165`, receipt `81a8b03fdd59003a118d2d1016f293fd8856ea1571876050c980c85618fe3205`); and the corrected 252-request render/token capacity audit is frozen at count-map `e5289d08fc2387e9fc1ad5a6c47c663f79ed4ef5387cbd8f43abf3cb8b2937cb`, evidence `7e7d358b24a9f386b27e981d51f958f5b72d05d06eb74089a6210d91a4f48cd6`, summary `803ca78c23702baced9261b86ccf41124185faf65cbd9686ad64812d1af6e2b2`, and receipt `921552ec7a711d583b8098a41cf482b771936915ca3f2ad698d47192ef674531`.
All 252 ablation prompts fit the frozen 49152 context / 2048 output / 1024 safety contract: maximum measured input is 11369 tokens against the 46080-token limit, with 0/252 over budget. The 32 unchanged counterevidence views match their frozen MINIMAL token counts exactly. The first token-capacity attempt is preserved as a pre-count harness canonicalization failure (failure SHA-256 `7cee6464f22ba4c33b7cf95276e9a490837bf2a87aecedca46af857c6ce4bf4f`); it was not a capacity falsification.
This documentation-only changeset is the formal Mandatory Ablation Mapping Freeze transition and becomes authoritative only after green exact-SHA CI. `mandatory_ablation_mappings_frozen=True` at that boundary, while `scored_execution_authorized=False` remains mandatory. Before any benchmark score-bearing request, the exact ablation score-bearing execution semantics (attempt matrix/repeats/order/seeds and external execution identity) must be frozen separately without reopening the preregistered mappings, scorer, thresholds, corpus, or FULL reference.**

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
