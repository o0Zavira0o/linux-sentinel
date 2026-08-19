# Sentinel-X

Sentinel-X is an **experimental Linux/systemd evidence framework for falsifiable operational reasoning**.

The project does **not** currently claim to be a production-grade RCA engine, autonomous remediation system, superior monitoring stack, or general causal inference engine. Its current research thesis is narrower:

> **Sentinel-X exists to make operational diagnoses falsifiable.**

Sentinel-X acquires bounded Linux/systemd/journald evidence, preserves provenance and missingness, creates controlled ground truth in a constrained fault lab, and tests whether structured evidence can prevent a human, heuristic, or LLM reasoner from publishing claims stronger than the available evidence supports.

## Current Position

- Development branch: `sentinel-x-phase1`
- Frozen implementation baseline: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`
- Completed implementation: Phase 0 through **Phase 5E.4**
- Active phase: **Phase 5F — Falsification**
- Last completed Phase-5F implementation milestone: **5F.6 — Ablation Scoring Adapter (`07330c4d68d149075ce0c906e4f50e046d9221b1`, exact-SHA CI `32262977658`)**
- Frozen Phase-5F.3A corrective checkpoint: **`ff0faa29dc0378263e84dcfbe74f5d46067a7f38` — Fedora validation, exact repository guards, push/remote equality, clean repository, and four-job CI all passed**
- Phase-5F.3 corpus milestone: **FROZEN — attempt #4 completed the full 16-run empirical campaign, derived all 20 adversarial cases, passed independent exported-artifact audit, and froze corpus-v1**
- Frozen corpus-v1 archive SHA-256: **`a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`**; **36 cases / 16 empirical / 20 adversarial / 28 HARD; no scored reasoner outputs**
- Phase-5F.4 milestone: **FROZEN at `3604fb3ed5571ee02f5a6e448628ae68bc201f73` — private strict JSON parser/validator, exact preregistered output boundary, Fedora validation, exact repository guards, push/remote equality, clean repository, and four-job CI run `32059127141` all passed; no scored output exists**
- Phase-5F.5 milestone: **FROZEN at implementation checkpoint `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5` — the private M1–M8 scorer passed authoritative Fedora validation (15/15 dedicated tests, 89/89 private Phase-5F regression, 1337/1337 full unit tests, 156-file format/lint scope, strict mypy over 84 source files) and exact-SHA CI run `32062765897`; formal docs-only freeze/5F.6 activation commit `5d8c1e227894a88a1f46de715b506903ceb58045` passed exact-SHA CI run `32105743211`; no scored reasoner output exists**
- Current milestone: **5F.6 — Ablation; generic execution preflight and deterministic-baseline common-scoring adapter are FROZEN. Corrective local Ollama/Gemma context checkpoint `29573586877ebcbc475ed2f00172129089e60fdd` passed exact-SHA CI run `32169631419` with context 49152 / max output 2048 / safety 1024. A clean blinded 108-count rebind, with `ZipFile.testzip()` prohibited and only RAW/MINIMAL/FULL member content opened, reproduces token-count SHA-256 `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2`, legacy 8192 falsification at 56/108 over budget, and corrected 49152 capacity at 0/108 over budget. The exact deterministic 324-attempt plan is frozen at `0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542` (file `61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80`), and the concrete-execution evidence chain is bound by `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3` (receipt `729367c756e8b7ac35c10ccfd50ac10fd0846face9e2bc1547813077ccf0a806`). Earlier `testzip()`-using count attempts are preserved as procedural history but are not the blinding authority. Mandatory ablation mappings are now evidence-complete before scoring: the seven field-level mappings are preregistered at SHA-256 `5a4a7188742788954c2d7b1d11085057ab56594327c8306305286d9ba1b41844` (receipt `07df5842df8d3f9e9f4f49156068daa15e8689b28895547b39429a69e4493054`); the private deletion-only implementation is CI-proven at `ea137456c97266c3c853a546e5af288b77008867` / exact-SHA CI run `32228043568`; the 252-coordinate visible-only realization is frozen at `2d6afb218e01f14e0e3ac46711a60a1816dad0688d0365b3bc24c4865100f490` (summary `e1764bcb93042f1ad7ff7a3f98306c679a99d8c52c8f1a4455d4d7581a606165`, receipt `81a8b03fdd59003a118d2d1016f293fd8856ea1571876050c980c85618fe3205`); and the corrected 252-request render/token capacity audit is frozen at count-map `e5289d08fc2387e9fc1ad5a6c47c663f79ed4ef5387cbd8f43abf3cb8b2937cb`, evidence `7e7d358b24a9f386b27e981d51f958f5b72d05d06eb74089a6210d91a4f48cd6`, summary `803ca78c23702baced9261b86ccf41124185faf65cbd9686ad64812d1af6e2b2`, and receipt `921552ec7a711d583b8098a41cf482b771936915ca3f2ad698d47192ef674531`.
All 252 ablation prompts fit the frozen 49152 context / 2048 output / 1024 safety contract: maximum measured input is 11369 tokens against the 46080-token limit, with 0/252 over budget. The 32 unchanged counterevidence views match their frozen MINIMAL token counts exactly. The first token-capacity attempt is preserved as a pre-count harness canonicalization failure (failure SHA-256 `7cee6464f22ba4c33b7cf95276e9a490837bf2a87aecedca46af857c6ce4bf4f`); it was not a capacity falsification.
The Formal Mandatory Ablation Mapping Freeze is closed at repository checkpoint `114a397eaf341cd044804220ed00a84fb0c8c70e`; `mandatory_ablation_mappings_frozen=True`. The private scorer-side mandatory-ablation adapter is CI-proven at `07330c4d68d149075ce0c906e4f50e046d9221b1` / exact-SHA CI run `32262977658`. It leaves frozen `scoring.py` and `ablation.py` unchanged, pins scorer-side execution semantics at SHA-256 `d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d`, requires exactly 756 already-captured outputs (7 mappings × 36 cases × 3 repeats), routes six mappings through frozen `minimal` and one through `full`, keeps mapping identity outside frozen scorer rows, and computes M8 independently per mapping. Authoritative Fedora validation passed 21/21 dedicated adapter tests, 176/176 private Phase-5F tests, and 1424/1424 full unit tests with 166 Python files in format/lint scope and strict mypy across 89 source files. This documentation-only changeset is the Formal Ablation Scoring Adapter Freeze transition and becomes authoritative only after its own green exact-SHA CI; `ablation_scoring_adapter_frozen=True` only at that boundary. `scored_execution_authorized=False` remains mandatory. Before any benchmark score-bearing request, the exact external ablation execution semantics (request matrix/repeats/order/seeds, retry/output-capture behavior, and external execution identity) must be frozen separately without reopening the mappings, scorer, scoring adapter, thresholds, corpus, or FULL reference.**
- Last authoritative implementation gate: **1424/1424 unit tests PASS**, **166 Python files** in the source/test format/lint scope, strict mypy over **89 source files PASS**; the mandatory-ablation scorer-side adapter checkpoint `07330c4d68d149075ce0c906e4f50e046d9221b1` passed 21/21 dedicated adapter tests, 176/176 private Phase-5F tests, exact repository/stage/content guards, push/local-remote equality, clean-repository verification, and exact-SHA four-job CI run `32262977658`. No benchmark score-bearing request or scored reasoner output exists.
- Phase 5E.4 controlled live proof: PASS
- Status: research/experimental; **not production-grade**

Phase 5F intentionally pauses feature growth. The next objective is to determine whether Sentinel-X structured evidence materially improves operational reasoning over simpler alternatives.

## Start Here

If you are a new engineer or a new AI session, do **not** infer the project from the source tree or this README alone.

Read in this order:

1. [`AGENTS.md`](AGENTS.md)
2. [`docs/START_HERE.md`](docs/START_HERE.md)
3. the documents referenced by `START_HERE.md`

`docs/START_HERE.md` is the canonical bootstrap document for continuing the project in a new conversation.

## What Is Implemented

The current repository includes:

- typed configuration, event, runtime, scheduling, EventBus, and JSONL evidence persistence;
- Linux host observation for CPU/load, memory, filesystem, disk I/O, network, and process state;
- bounded systemd service-state observation;
- bounded journald observation with cursor/checkpoint continuity;
- systemd/journal correlation with explicit evidence quality;
- conservative systemd service detection with `HEALTHY`, `INACTIVE`, `FAILED`, and `UNASSESSED`;
- stateful incident lifecycle and live EventBus integration;
- a controlled `sentinel-x-lab-*` systemd Fault Lab with recovery verification and ground truth;
- controlled detection benchmarking and evidence characterization;
- systemd dependency evidence/discovery/graph/versioning;
- bounded propagation evidence with positive, negative, insufficient, and counterevidence semantics;
- controlled Requires/Wants propagation experiments;
- conservative candidate-local and paired evidence synthesis;
- pre-execution protocol provenance and protocol-bound controlled live execution;
- private Phase-5F blinded RAW/MINIMAL/FULL projection with hidden-gold separation;
- private deterministic Phase-5F comparators: B0 state rule, B1 Graph+Time, and B1S frozen-synthesis projection;
- private frozen Phase-5F M1–M8 scorer with exact 324-attempt LLM matrix validation and preregistered analysis slices;
- private Phase-5F.6 deterministic-baseline scoring adapter frozen at `6d86fff7d0699ca4d11d48cdd98538ed81eb0380` with exact-SHA CI run `32123764282`, preserving B0/B1/B1S identity outside the frozen scorer row while reusing the frozen scorer with each baseline's actual evidence projection;
- private Phase-5F.6 provider-neutral generic execution preflight frozen at `39d1db2845f6d461ab21a6da2077be2b77bf5982` with exact-SHA CI run `32108226447`; it binds prompt/schema/frozen-artifact identities, stateless request construction, a one-retry transport policy, deterministic 324-attempt planning, and explicit no-truncation token-capacity checks while performing no provider call.
- private Phase-5F.6 local Ollama/Gemma execution-controls candidate that pins Ollama/model/GGUF identity and service isolation, constructs future score payloads without sending them, and exposes only non-score-bearing identity, render-only, and tokenizer-only preflight transport;
- private Phase-5F.6 scorer-side mandatory-ablation adapter, CI-proven at `07330c4d68d149075ce0c906e4f50e046d9221b1` / `32262977658`, that preserves the frozen scorer, validates the exact 7×36×3 captured-output matrix, keeps mapping identity outside scorer rows, and aggregates M8 independently per mapping;

The Phase-5 dependency/reasoning subsystem remains a **research subsystem** and is not part of the normal `sentinel-x run` decision path.

## What Is Not Proven

The repository does **not** currently prove:

- superiority over simple state/graph/time heuristics;
- improvement over a strong LLM given raw operational evidence;
- production false-positive/false-negative rates;
- cross-distro or cross-systemd generalization;
- production scalability or long-running stability;
- statistically calibrated probabilities or confidence scores;
- general causal validity or universal fault-propagation laws;
- autonomous remediation safety.

These are intentionally visible unknowns rather than hidden claims.

## Phase 5F — Falsification

Phase 5F first freezes a 36-case corpus (16 empirical controlled executions plus 20 deterministic adversarial derivatives) and then compares the same blinded cases using:

- B0: state/rule baseline;
- B1: simple graph + time heuristic;
- B2: fresh LLM with RAW evidence;
- B3: same LLM/task with MINIMAL Sentinel-X evidence;
- B4: same LLM/task with FULL current Phase-5 evidence.

Primary evaluation includes correctness, propagation Macro-F1, unsupported causal claim rate, correct abstention, evidence citation precision, provenance violations, counterevidence preservation, and run-to-run consistency.

Full Phase-5 complexity survives only if it provides repeatable value over MINIMAL evidence. If it does not, the architecture is expected to shrink.

See [`research/phase5f/THESIS.md`](research/phase5f/THESIS.md) and [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Engineering Quality Gate

Before a code change is frozen:

```bash
./scripts/check.sh
```

The current gate performs:

1. Python syntax validation
2. Ruff formatting check
3. Ruff linting
4. strict mypy checking
5. unit tests

Linux-native behavior also requires an appropriate Fedora live/smoke proof when the change affects real system behavior. Green unit tests are a merge prerequisite, **not proof of product or research value**.

## Development Setup

Requirements:

```text
Linux
Python >= 3.11
```

Typical setup:

```bash
git clone https://github.com/o0Zavira0o/linux-sentinel.git
cd linux-sentinel
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools
python -m pip install -e ".[dev]"
```

Validate the environment:

```bash
sentinel-x doctor
```

Validate configuration:

```bash
sentinel-x config-check --config sentinel.example.toml
```

Run the current operational runtime:

```bash
sentinel-x run
```

## Core Research Principles

- Linux/systemd provide execution and observable state; Sentinel-X is an evidence/intelligence layer.
- Preserve native evidence before interpretation.
- Prefer duplicate evidence to silent loss when at-least-once semantics apply.
- Unknown state stays explicit.
- Missing evidence is not zero/healthy/no-effect.
- Topology is not runtime effect.
- Temporal order is not causation.
- A single intervention is not a universal causal law.
- No invented confidence or probability.
- Bounded structures fail explicitly rather than silently discarding important evidence.
- Claims must not exceed evidence.

## Documentation

The documentation system is part of the engineering process:

- `docs/PROJECT_SPEC.md` — what the project is meant to become
- `docs/ARCHITECTURE.md` — what architecture exists now
- `docs/CURRENT_STATE.md` — what is implemented right now
- `docs/ROADMAP.md` — where the project goes next
- `docs/CONSTRAINTS_AND_INVARIANTS.md` — what must not accidentally change
- `docs/KNOWN_EXCEPTIONS.md` — odd-looking but intentional/current behavior
- `docs/TECH_DEBT.md` — real debt that should not necessarily be fixed now
- `docs/TESTING_STRATEGY.md` — what each validation layer proves
- `docs/phases/` — phase-specific intent/history/status
- `docs/adr/` — architectural decisions and why they were made

Every meaningful push must evaluate whether these documents need an update. See `AGENTS.md`.

## Repository Status

Sentinel-X remains an **experimental framework**. The current goal is not to add more features. The current goal is to give the project a fair opportunity to falsify its own evidence thesis before further architecture is built.
