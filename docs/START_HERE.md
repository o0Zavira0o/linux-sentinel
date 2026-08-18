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
- Phase 5F.2: **FROZEN — deterministic baselines established**
- Phase 5F.3A: **THIRD CORRECTIVE CHECKPOINT FROZEN at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38` — dedicated, Phase-5F, full-project, research-harness static, exact repository/content, push/remote-equality, clean-repository, and four-job CI guards all passed**
- Phase 5F.3B: **FROZEN — attempt #4 completed the fresh 16-run empirical campaign, derived all 20 adversarial cases, passed independent exported-artifact audit, and froze corpus-v1 with archive SHA-256 `a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`; no scored reasoner output exists**
- Phase 5F.4: **FROZEN at `3604fb3ed5571ee02f5a6e448628ae68bc201f73` — structured reasoner output boundary passed Fedora validation, exact repository guards, push/remote equality, clean-repository verification, and four-job CI run `32059127141`; no scored output exists**
- Phase 5F.5: **FROZEN at `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5` — private M1–M8 scorer passed Fedora validation, exact repository/stage guards, push/remote equality, clean-repository verification, and four-job exact-SHA CI run `32062765897`; no scored reasoner output exists**
- Current milestone: **5F.6 — Ablation; generic execution preflight and deterministic-baseline common-scoring adapter are FROZEN. The local Ollama/Gemma Concrete Execution candidate is now Fedora/LIVE-VALIDATED with implementation checkpoint pending. Exact GGUF/model identities remain pinned; model-store privacy was preserved; render-only plus tokenizer-only replay reproduced the synthetic `prompt_eval_count` exactly at 76 tokens; and context 8192 passed at 87% GPU with zero swap. A Python 3.14 bodyless-GET headers defect found by the first live identity proof was corrected and fully regression-tested. The candidate still has no score-bearing transport. Exact 108 frozen-corpus token counts, the concrete 324-attempt plan, mandatory-ablation mappings, deterministic scores, and LLM scores remain absent.**
- Last completed implementation milestone: **Phase 5F.6 — Deterministic Baseline Common-Scoring Adapter (formally frozen)**
- Last completed research-artifact milestone: **Phase 5F.3B — Live Corpus Capture & Freeze**
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
- private Phase-5F RAW/MINIMAL/FULL projection from one gold-free case source, with hidden `CaseGold` kept outside visible exports;
- private deterministic B0/B1/B1S comparators ready for later scoring, with no benchmark value conclusion assigned yet;
- private frozen Phase-5F M1–M8 scorer with exact 324-attempt matrix validation and preregistered analysis slices;
- frozen Phase-5F corpus-v1: 36 cases / 16 empirical / 20 adversarial / 28 HARD, with hidden/visible separation and source-run lineage independently audited before scoring.

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
2. deterministic B0/B1/B1S comparators exist but have not yet been scored; no blinded LLM baseline has been executed;
3. FULL Phase-5 complexity has not been ablated against MINIMAL evidence;
4. documentation was historically stale and is now being made an engineering artifact;
5. Phase-5 dependency public surface and identity/validation machinery are reduction candidates, but must not be preemptively refactored.

## Immediate Next Work

1. checkpoint and exact-SHA CI the Fedora/LIVE-VALIDATED local Ollama/Gemma Concrete Execution candidate without generating any score-bearing output;
2. measure all 108 case-condition semantic requests through the validated render/tokenizer path, confirm or revise the pre-score context budget, and freeze the resulting 324-attempt no-truncation plan;
3. freeze the exact corpus-specific mechanical view construction for every mandatory 5F.6 field ablation before inspecting any benchmark score;
4. execute B2 RAW, B3 MINIMAL, B4 FULL, and the mandatory ablations with fresh isolated requests and logged exact-request transport retries only;
5. parse and score with the frozen 5F.4/5F.5 boundaries, preserving invalid structured outputs, missingness, provenance violations, counterevidence, and pseudoreplication grouping;
6. evaluate the deterministic B0/B1/B1S comparators on the frozen corpus without changing their frozen implementations;
7. apply the frozen 5F.7 A/B/C/D/E decision rules without post-hoc threshold or subset changes;
8. enter 5R only if the evidence thesis survives and the frozen roadmap authorizes reduction.

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
10. [`../research/phase5f/THESIS.md`](../research/phase5f/THESIS.md) and the other frozen Phase-5F preregistration documents
11. [`../research/phase5f/CORPUS_V1_CAPTURE.md`](../research/phase5f/CORPUS_V1_CAPTURE.md) as the frozen execution/capture protocol that produced corpus-v1; preserve its historical pre-success status wording rather than rewriting the protocol after the observed live result
12. relevant ADRs from [`adr/README.md`](adr/README.md)

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

- Date: 2026-08-18
- Phase: 5F.6 ablation / blinded comparative evaluation
- Frozen FULL reference: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Frozen Phase-5F.2 checkpoint: `295a57975253441dc4e2a18290ce5043a57e938a`
- Frozen Phase-5F.5 scorer checkpoint: `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`
- Phase-5F.5 exact-SHA CI run: `32062765897`
- Phase-5F.5 formal freeze / 5F.6 activation commit: `5d8c1e227894a88a1f46de715b506903ceb58045`
- Phase-5F.5 formal-freeze CI run: `32105743211`
- Update reason: local Ollama/Gemma execution controls are now Fedora/LIVE-VALIDATED. After the Python 3.14 GET-header compatibility correction, 18 dedicated tests, 34 execution+adapter tests, 135 private Phase-5F tests, and the full 1383-test gate passed. Runtime identity, private service-account model-store access, exact 76-token render/tokenizer equivalence, and context-8192 resource feasibility all passed without score-bearing corpus inference. The next engineering boundary is the implementation checkpoint/CI, followed by the exact 108-count map and no-truncation plan; benchmark scores remain absent.
