# Project Bootstrap — START HERE

This is the compact bootstrap context for a completely new engineer or AI session.

## Project

- Name: Sentinel-X
- Repository: `https://github.com/o0Zavira0o/linux-sentinel`
- Development branch: `sentinel-x-phase1`
- Frozen implementation baseline: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Primary development environment: Fedora Linux
- Language: Python >= 3.11
- Current status: experimental research framework; not production-grade

## One-Paragraph Summary

Sentinel-X is a Linux/systemd evidence framework whose current research thesis is that operational diagnoses should be **falsifiable and bounded by evidence**. It captures bounded Linux/systemd/journald evidence, preserves provenance and missingness, creates controlled fault ground truth, and is now testing whether RAW, MINIMAL structured, or FULL Phase-5 evidence materially changes the correctness and epistemic safety of heuristic/LLM reasoning. The system does not currently claim production RCA superiority, calibrated causal confidence, general Linux causal inference, or autonomous remediation.

## Current Development Position

- Current phase: **Phase 5F — Falsification**
- Phase 5F.0: **FROZEN — preregistration baseline established**
- Phase 5F.1: **FROZEN — blinded projection boundary established**
- Current milestone: **5F.2 — Deterministic Baselines**
- Last completed implementation milestone: **Phase 5E.4 — Protocol-Bound Controlled Live Execution**
- Next milestone after 5F.2: **5F.3 — Blind Falsification Corpus**
- Previous planned 5E.5+ feature-growth sequence: **superseded**

## What Currently Works

- typed runtime/config/EventBus/JSONL evidence foundation;
- host observability collectors;
- systemd service observation;
- journald observation and restart-safe cursor/checkpoint continuity;
- systemd/journal correlation;
- conservative service detection and incident lifecycle;
- controlled systemd Fault Lab with verified recovery;
- detection evaluation/benchmarking/evidence characterization;
- systemd dependency evidence/discovery/graph/versioning;
- temporal propagation and sampling coverage evidence;
- controlled Requires/Wants pair experiments and live runner;
- conservative evidence synthesis and paired contrast;
- pre-execution protocol provenance;
- protocol-bound live execution with boot/backend binding;
- private Phase-5F RAW/MINIMAL/FULL projection from one gold-free case source, with hidden `CaseGold` kept outside visible exports.

Frozen Phase-5E.4 FULL-reference Fedora baseline:

```text
140 Python files formatted
74 source files checked by mypy
1248 / 1248 unit tests PASS
4 GitHub Actions jobs green
```

## Most Important Architectural Facts

1. `systemd` performs lifecycle execution; Sentinel-X reasons about evidence above native Linux mechanisms.
2. The shared EventBus/runtime remains the operational integration path; no competing bus should be introduced.
3. systemd service-state observations are intentionally nontransactional; detection bridge failure handling protects later evidence.
4. Journald evidence follows at-least-once continuity semantics; duplicate evidence is preferable to silent loss.
5. Detection explicitly preserves `UNASSESSED` rather than forcing classification.
6. Phase 5 dependency/reasoning code is currently a research subsystem, not a normal CLI runtime consumer.
7. Phase 5E.4 is frozen intact as the FULL ablation reference; do not simplify it before 5F results.
8. The future Claim Gate is NOT implemented yet and must not be implemented before evidence value is measured.

## Critical Invariants

Read `CONSTRAINTS_AND_INVARIANTS.md`. Highest-impact rules:

- evidence before interpretation;
- no silent evidence loss when at-least-once applies;
- no invented probability/confidence;
- missing evidence stays missing;
- topology != runtime effect;
- temporal ordering != cause;
- counterevidence remains visible;
- negative claims require sufficient bounded coverage;
- cross-boot/scope evidence cannot silently mix;
- one controlled intervention does not establish a universal causal law;
- bounded structures fail explicitly instead of silently evicting evidence.

## Known Exceptions

See [`KNOWN_EXCEPTIONS.md`](KNOWN_EXCEPTIONS.md). Important examples:

- current Phase-5 public API/identity complexity is intentionally retained until Full-vs-Minimal ablation;
- incident snapshots are not claimed hard-crash durable;
- detection bridge pending queues are in-memory;
- controlled lab proofs may use operator-authorized sudo and are not yet a production privilege model;
- inactive transition timing can be unavailable and must not be fabricated.

## Current High-Priority Problems

1. external value of structured evidence is unproven;
2. no simple Graph+Time or blinded LLM baseline has yet been scored;
3. FULL Phase-5 complexity has not been ablated against MINIMAL evidence;
4. documentation was historically stale and is now being made an engineering artifact;
5. Phase-5 dependency public surface and identity/validation machinery are reduction candidates, but must not be preemptively refactored.

## Immediate Next Work

1. implement preregistered deterministic baselines B0, B1 Graph+Time, and B1S current-synthesis projection on the frozen 5F.1 evidence-bundle boundary;
2. build the blinded falsification corpus while keeping empirical and adversarial cases distinct;
3. execute isolated RAW/MINIMAL/FULL reasoner comparisons;
4. score preregistered primary metrics and ablations;
5. issue one required Phase-5F verdict;
6. only if the thesis survives, perform Phase 5R deletion-first reduction.

## Future-Sensitive Areas

Do not casually modify:

- `src/sentinel_x/dependency/` — frozen FULL reference for ablation;
- Fault Lab experiment identity/ground-truth/recovery semantics;
- journald cursor/checkpoint continuity;
- EventBus publication/failure semantics;
- detection bridge pending/deferred ordering;
- boot identity normalization/binding;
- evidence missingness/coverage semantics;
- current claim-boundary serialization used as Full Phase-5 evidence.

## Mandatory Reading Order

After this file read:

1. [`PROJECT_SPEC.md`](PROJECT_SPEC.md)
2. [`ARCHITECTURE.md`](ARCHITECTURE.md)
3. [`CURRENT_STATE.md`](CURRENT_STATE.md)
4. [`ROADMAP.md`](ROADMAP.md)
5. [`CONSTRAINTS_AND_INVARIANTS.md`](CONSTRAINTS_AND_INVARIANTS.md)
6. [`KNOWN_EXCEPTIONS.md`](KNOWN_EXCEPTIONS.md)
7. [`TECH_DEBT.md`](TECH_DEBT.md)
8. [`TESTING_STRATEGY.md`](TESTING_STRATEGY.md)
9. [`phases/PHASE-05-EVIDENCE-AND-FALSIFICATION.md`](phases/PHASE-05-EVIDENCE-AND-FALSIFICATION.md)
10. [`../research/phase5f/THESIS.md`](../research/phase5f/THESIS.md) before any Phase-5F benchmark implementation
11. relevant ADRs from [`adr/README.md`](adr/README.md)

For work touching earlier phases, read that phase document before changing code.

## Warning for AI Agents

Do not infer architectural intent solely from current code. A strange-looking implementation may be frozen experiment evidence, a known exception, technical debt, or a deliberate cross-phase invariant.

Do not defend sunk cost. Do not refactor sunk cost before the falsification experiment measures whether it has value.

## New Conversation Bootstrap Instruction

A new conversation should start with:

```text
Read /AGENTS.md and /docs/START_HERE.md first.
Then read all documentation referenced by START_HERE before making any architectural or implementation recommendation.
Treat accepted ADRs, documented invariants, roadmap dependencies, known exceptions, and Phase-5F preregistration as mandatory context.
Reconstruct project goal, current architecture, completed phases, current phase, remaining work, roadmap, constraints, exceptions, and technical debt before continuing development.
```

## Last Updated

- Date: 2026-08-16
- Phase: 5F.2
- Last verified implementation baseline: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Update reason: Phase-5F.1 blinded projection boundary completed; transition to deterministic baselines
