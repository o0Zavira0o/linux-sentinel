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
- Last completed Phase-5F implementation milestone: **5F.3A — Corpus Contract & Capture Harness**
- Current milestone: **5F.3A corrective checkpoint finalization — the third live-discovered multiple-candidate hidden-gold/live-shape correction passed Fedora validation; 5F.3B remains paused until repository freeze conditions pass**
- Validated corrective gate: **1308/1308 unit tests PASS**, **152 Python files** in the source/test format/lint scope, strict mypy over **82 source files PASS**; `research/phase5f/capture_corpus_v1.py` also passed its separate compile/Ruff/direct-mypy validation because `scripts/check.sh` intentionally excludes `research/`
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
- private deterministic Phase-5F comparators: B0 state rule, B1 Graph+Time, and B1S frozen-synthesis projection.

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
