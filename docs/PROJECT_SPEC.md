# Project Specification

## Vision

Sentinel-X aims to become an **evidence-integrity and claim-bounding layer for operational reasoning**.

Its purpose is not to be the reasoner with the most confident root-cause answer. Its purpose is to make operational claims inspectable, scoped, and falsifiable by linking them to what was actually observed, what was missing, what contradicted the claim, and what was established under controlled intervention.

## Problem Being Solved

Operational diagnosis often collapses different epistemic categories into one statement:

- observed state;
- topology/dependency declaration;
- temporal sequence;
- correlation;
- negative observation;
- missing evidence;
- controlled intervention;
- causal hypothesis;
- causal conclusion.

This makes human and automated diagnoses difficult to audit and easy to overstate.

Sentinel-X tests the proposition that **machine-verifiable evidence scope and missingness can materially improve operational reasoning and reduce unsupported claims**.

## Target Users

Potential future users, if the thesis survives evaluation:

- SRE and reliability engineers;
- platform engineering teams;
- systems/AIOps researchers;
- builders of AI-assisted operations agents;
- teams requiring an auditable evidence trail for operational conclusions.

These are target users, not current validated customers.

## Primary Use Cases

### UC-001 — Evidence-preserving incident investigation
Capture Linux/systemd/journald evidence with explicit source, time, boot, and missingness context.

### UC-002 — Controlled fault ground truth
Run safe Sentinel-owned systemd experiments with known intervention windows and verified recovery.

### UC-003 — Evidence-constrained reasoning evaluation
Evaluate heuristics or LLMs on the same blinded operational evidence and measure unsupported claims, abstention, provenance use, and counterevidence preservation.

### UC-004 — Future claim verification
If Phase 5F validates the thesis, reasoners will emit structured claim drafts that can later be checked by a reasoner-agnostic Claim Gate.

## Functional Requirements — Current / Near-Term

### FR-001 — Bounded Linux evidence acquisition
Convert supported native Linux/systemd/journal sources into typed evidence without silently inventing missing data.

### FR-002 — Provenance preservation
Evidence used for reasoning retains enough identity/scope metadata to reject incompatible boot/context mixing.

### FR-003 — Explicit uncertainty/missingness
Unknown, unavailable, insufficient, and counterevidence states remain visible.

### FR-004 — Controlled ground truth
The Fault Lab can run allowed interventions on Sentinel-owned fixtures and verify recovery.

### FR-005 — Reproducible evaluation
The project can compare identical cases across deterministic baselines and blinded external reasoners.

### FR-006 — Claim-strength evaluation
Phase 5F measures whether reasoners publish unsupported causal/negative claims, preserve counterevidence, and abstain correctly.

## Functional Requirements — Conditional Future

These activate only if Phase 5F validates evidence value and Phase 5R reduces unnecessary complexity.

### FR-101 — ClaimDraft interface
A reasoner can return structured operational claims without being trusted as final truth.

### FR-102 — Claim Gate
A reasoner-agnostic verifier can accept, downgrade, contradict, mark insufficient, or reject claims based on evidence scope and policy.

### FR-103 — Evidence-Carrying Diagnosis
Accepted diagnosis output contains claims plus support/counterevidence/provenance and visible epistemic status.

### FR-104 — External ecosystem adapters
Prefer ingestion/adapters for standard telemetry and chaos backends rather than recreating complete ecosystems.

## Non-Functional Requirements

### Reliability
- evidence delivery semantics are explicit;
- bounded structures do not silently discard critical evidence;
- state that requires crash durability must declare and test it.

### Security / Safety
- unsafe systemd unit identifiers/paths are rejected;
- controlled mutation is limited to explicit lab fixtures;
- production privilege separation is required before production claims.

### Maintainability
- new abstractions require an independent invariant and real consumers;
- public API defaults to internal until stability/consumer need is proven;
- primitive validation is a Phase-5R centralization candidate, not a Phase-5F refactor.

### Compatibility
- Python >=3.11;
- Fedora is the primary validated environment today;
- cross-distro/systemd generalization is not yet claimed.

### Performance
No production performance SLO is currently claimed. Performance/soak work follows thesis validation.

## Product Constraints

- Linux-native/systemd-centered current scope;
- no arbitrary shell execution as an intelligence interface;
- no numeric confidence without valid calibration;
- no autonomous remediation before diagnosis/claim validity, privilege model, and crash behavior are validated.

## Explicit Non-Goals — Current

Until Phase 5F decision:

- new Linux metric collectors;
- broad chaos-engineering functionality;
- generic distributed tracing implementation;
- generic AI/RCA engine;
- autonomous remediation;
- UI/dashboard;
- plugin ecosystem;
- probabilistic confidence scoring;
- new dependency relations or protocol layers;
- expansion of the current Phase-5 public evidence hierarchy.

## Domain Terminology

**Observation** — typed representation of something actually read from a source.

**Evidence** — observation or derived record with explicit provenance/scope relevant to a question.

**Ground truth** — controlled experiment truth anchored to an actual intervention window, not detector output.

**Missing evidence** — required measurement unavailable; not zero/healthy/no-effect.

**Bounded negative evidence** — no expected effect observed under explicitly sufficient bounded sampling; not a universal no-effect claim.

**Counterevidence** — evidence conflicting with a proposed direction/claim and kept visible.

**Causal hypothesis** — candidate explanation evidence permits considering but does not establish as cause.

**Evidence-Carrying Diagnosis (future)** — structured diagnosis whose claims retain support, counterevidence, scope, and verification outcome.

## Success Criteria

### Phase-5F thesis survival
Structured evidence produces a preregistered material improvement over RAW reasoning on the hard falsification corpus without unacceptable primary-metric regression.

### Full architecture survival
FULL Phase-5 evidence provides repeatable independent benefit over MINIMAL evidence; otherwise unnecessary machinery becomes a deletion/internalization candidate.

### Longer-term success
Sentinel-X is successful if it makes unsupported operational conclusions detectable and downgradeable/rejectable in a way that improves operator/agent outcomes—not merely if it emits a root-cause label.

## Business / Commercialization Principle

Commercialization is conditional on measured technical value. A plausible differentiated product, if the thesis survives, is an **Evidence Integrity Layer for AI-assisted Operations**: agents may investigate freely, but accepted operational diagnoses remain auditable and evidence-constrained.

Do not productize generic monitoring features simply because the market has them.
