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
- Current milestone: **5F.6 — Ablation; generic execution preflight and deterministic-baseline common-scoring adapter are FROZEN. Corrective local Ollama/Gemma context checkpoint `29573586877ebcbc475ed2f00172129089e60fdd` / CI `32169631419` is CI-PROVEN with manifest `e0da42efc5e67fc3d86b434a99e3e46dda257ba1a8b049c2adbb408e79582ee6` and context 49152 / output 2048 / safety 1024. The authoritative clean blinded 108-count rebind `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2` blocks `ZipFile.testzip()`, reads member content only from RAW/MINIMAL/FULL, records zero hidden-member content reads, reproduces legacy 8192 failure at 56/108, and yields 0/108 corrected capacity violations. The exact deterministic 324-attempt plan `0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542` / file `61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80` is FROZEN and the complete chain is bound by `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3` / receipt `729367c756e8b7ac35c10ccfd50ac10fd0846face9e2bc1547813077ccf0a806`. Earlier `testzip()`-using attempts remain preserved as numeric/procedural history but are not the blinding authority. Mandatory ablation mappings are now evidence-complete before scoring: the seven field-level mappings are preregistered at SHA-256 `5a4a7188742788954c2d7b1d11085057ab56594327c8306305286d9ba1b41844` (receipt `07df5842df8d3f9e9f4f49156068daa15e8689b28895547b39429a69e4493054`); the private deletion-only implementation is CI-proven at `ea137456c97266c3c853a546e5af288b77008867` / exact-SHA CI run `32228043568`; the 252-coordinate visible-only realization is frozen at `2d6afb218e01f14e0e3ac46711a60a1816dad0688d0365b3bc24c4865100f490` (summary `e1764bcb93042f1ad7ff7a3f98306c679a99d8c52c8f1a4455d4d7581a606165`, receipt `81a8b03fdd59003a118d2d1016f293fd8856ea1571876050c980c85618fe3205`); and the corrected 252-request render/token capacity audit is frozen at count-map `e5289d08fc2387e9fc1ad5a6c47c663f79ed4ef5387cbd8f43abf3cb8b2937cb`, evidence `7e7d358b24a9f386b27e981d51f958f5b72d05d06eb74089a6210d91a4f48cd6`, summary `803ca78c23702baced9261b86ccf41124185faf65cbd9686ad64812d1af6e2b2`, and receipt `921552ec7a711d583b8098a41cf482b771936915ca3f2ad698d47192ef674531`.
All 252 ablation prompts fit the frozen 49152 context / 2048 output / 1024 safety contract: maximum measured input is 11369 tokens against the 46080-token limit, with 0/252 over budget. The 32 unchanged counterevidence views match their frozen MINIMAL token counts exactly. The first token-capacity attempt is preserved as a pre-count harness canonicalization failure (failure SHA-256 `7cee6464f22ba4c33b7cf95276e9a490837bf2a87aecedca46af857c6ce4bf4f`); it was not a capacity falsification.
The Formal Mandatory Ablation Mapping Freeze is closed at repository checkpoint `114a397eaf341cd044804220ed00a84fb0c8c70e`; `mandatory_ablation_mappings_frozen=True`. The private scorer-side mandatory-ablation adapter is CI-proven at `07330c4d68d149075ce0c906e4f50e046d9221b1` / exact-SHA CI run `32262977658`. It leaves frozen `scoring.py` and `ablation.py` unchanged, pins scorer-side execution semantics at SHA-256 `d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d`, requires exactly 756 already-captured outputs (7 mappings × 36 cases × 3 repeats), routes six mappings through frozen `minimal` and one through `full`, keeps mapping identity outside frozen scorer rows, and computes M8 independently per mapping. Authoritative Fedora validation passed 21/21 dedicated adapter tests, 176/176 private Phase-5F tests, and 1424/1424 full unit tests with 166 Python files in format/lint scope and strict mypy across 89 source files. This documentation-only changeset is the Formal Ablation Scoring Adapter Freeze transition and becomes authoritative only after its own green exact-SHA CI; `ablation_scoring_adapter_frozen=True` only at that boundary. `scored_execution_authorized=False` remains mandatory. Before any benchmark score-bearing request, the exact external ablation execution semantics (request matrix/repeats/order/seeds, retry/output-capture behavior, and external execution identity) must be frozen separately without reopening the mappings, scorer, scoring adapter, thresholds, corpus, or FULL reference. Deterministic scores and all LLM benchmark scores remain absent.**
- Last completed implementation milestone: **Phase 5F.6 — Ablation Scoring Adapter (`07330c4d68d149075ce0c906e4f50e046d9221b1`, exact-SHA CI `32262977658`)**
- Last completed research-artifact milestone: **Phase 5F.6 — Frozen 252-Coordinate Ablation Realization + Corrected 252-Request Token-Capacity Audit**
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
- private CI-proven Phase-5F.6 mandatory-ablation scorer-side adapter at `07330c4d68d149075ce0c906e4f50e046d9221b1` / `32262977658`, preserving the frozen scorer while validating and aggregating the exact 756-output ablation matrix;
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

1. complete this documentation-only Formal Ablation Scoring Adapter Freeze transition and require green exact-SHA CI before treating the adapter freeze as authoritative;
2. freeze the exact external score-bearing execution semantics for all mandatory ablation views without changing the already frozen field mappings, frozen scorer, or scorer-side adapter;
3. keep `scored_execution_authorized=False` until that external execution boundary is content-addressed and independently audited;
4. only after that freeze, execute B2 RAW, B3 MINIMAL, B4 FULL, and mandatory ablations with fresh isolated requests and logged exact-request transport retries only;
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

- Date: 2026-08-19
- Phase: 5F.6 ablation / blinded comparative evaluation
- Frozen FULL reference: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Frozen Phase-5F.2 checkpoint: `295a57975253441dc4e2a18290ce5043a57e938a`
- Frozen Phase-5F.5 scorer checkpoint: `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`
- Phase-5F.5 exact-SHA CI run: `32062765897`
- Phase-5F.5 formal freeze / 5F.6 activation commit: `5d8c1e227894a88a1f46de715b506903ceb58045`
- Phase-5F.5 formal-freeze CI run: `32105743211`
- Update reason: corrective context checkpoint `29573586877ebcbc475ed2f00172129089e60fdd` is now exact-SHA CI-proven by run `32169631419`; the authoritative clean blinded rebind prohibits `ZipFile.testzip()`, opens only the three visible streams, reproduces count map `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2`, and demonstrates 56/108 legacy 8192 violations versus 0/108 corrected 49152 violations. The exact deterministic 324-attempt plan is frozen at `0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542` / file `61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80`, and the full concrete-execution chain is bound by `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3` / receipt `729367c756e8b7ac35c10ccfd50ac10fd0846face9e2bc1547813077ccf0a806` with scored execution still explicitly unauthorized. Earlier `testzip()`-using count attempts remain preserved but superseded for blinding claims. Mandatory ablation mappings are now evidence-complete through the seven-field preregistration, CI-proven implementation, 252-coordinate realization, and corrected 252-request capacity audit. This docs-only checkpoint is their formal repository freeze transition conditional on exact-SHA CI; score-bearing ablation execution semantics are next and benchmark scores remain absent.
