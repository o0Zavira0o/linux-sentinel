# Sentinel-X

Sentinel-X is a Linux-native research platform for building safe,
observable, and eventually autonomous fault detection, diagnosis,
and remediation systems.

The project is being developed incrementally from the original
`linux-sentinel` prototype.

Sentinel-X is currently in its engineering-baseline phase. It does
not yet provide production-ready anomaly detection, root-cause
analysis, or automated remediation.

---

## Project Direction

The long-term control loop is:

```text
Observe
   ↓
Detect
   ↓
Diagnose
   ↓
Estimate uncertainty
   ↓
Apply safety policy
   ↓
Remediate
   ↓
Verify
   ↓
Learn
```

The design goal is to keep the execution layer separate from the
intelligence layer.

Native Linux mechanisms such as systemd will eventually perform
service lifecycle operations. Sentinel-X will provide observation,
reasoning, safety policy, and verification above those mechanisms.

---

## Current Architecture

The current Phase 0 architecture contains:

```text
CLI
 │
 ▼
Typed Configuration
 │
 ▼
SentinelEngine
 │
 ├── Lifecycle State Machine
 │
 └── EventBus
        │
        ├── Console Subscriber
        │
        └── JSONL Event Recorder
                 │
                 ▼
          Durable Event Store
```

Implemented components include:

- Python package structure
- command-line interface
- environment doctor
- strict TOML configuration
- typed immutable events
- lifecycle state machine
- in-process EventBus
- subscriber failure isolation
- graceful SIGINT/SIGTERM shutdown
- structured JSONL event persistence
- run IDs
- event schema versions
- private event-file permissions
- unit tests
- Ruff linting
- strict mypy type checking
- GitHub Actions quality checks

---

## Requirements

Sentinel-X currently requires:

```text
Linux
Python >= 3.11
```

Fedora Linux is the primary development environment.

---

## Development Setup

Clone the repository and enter it:

```bash
git clone <repository-url>
cd linux-sentinel
```

Create a virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Install Sentinel-X together with development tools:

```bash
python -m pip install --upgrade pip setuptools
python -m pip install -e ".[dev]"
```

---

## Environment Check

Run:

```bash
sentinel-x doctor
```

A valid Fedora/Linux development environment should report that the
Linux platform and Python requirements pass.

---

## Configuration

Sentinel-X uses TOML configuration.

The tracked example is:

```text
sentinel.example.toml
```

Create a local configuration:

```bash
cp sentinel.example.toml sentinel.toml
```

The local `sentinel.toml` file is intentionally ignored by Git.

Current example:

```toml
[agent]
instance_name = "sentinel-x"
tick_interval = 0.5

[storage]
enabled = true
directory = "~/.local/state/sentinel-x/events"
flush_on_write = true
```

Validate configuration without starting the agent:

```bash
sentinel-x config-check
```

Validate a specific file:

```bash
sentinel-x config-check --config /path/to/config.toml
```

---

## Running Sentinel-X

Start the current core runtime:

```bash
sentinel-x run
```

Stop it gracefully with:

```text
Ctrl+C
```

Sentinel-X converts SIGINT and SIGTERM into graceful shutdown
requests.

---

## Event Storage

When event persistence is enabled, every runtime execution receives
a unique `run_id`.

Each run writes to a separate JSON Lines file.

The default location is:

```text
~/.local/state/sentinel-x/events
```

Each persisted record contains information such as:

```text
schema_version
run_id
instance_name
recorded_at
event_id
occurred_at
event kind
severity
source
message
attributes
```

The event store is intended to become the evidence layer for future
fault-injection experiments, anomaly detection, temporal analysis,
root-cause analysis, and remediation evaluation.

---

## Engineering Quality Gate

Before committing Sentinel-X changes, run:

```bash
./scripts/check.sh
```

The current gate performs:

```text
Python syntax validation
        ↓
Ruff linting
        ↓
strict mypy checking
        ↓
unit tests
```

Any failed stage stops the gate immediately.

GitHub Actions executes the same gate on supported Python versions.

---

## Unit Tests

Tests can also be run directly:

```bash
python -m unittest discover -s tests/unit -v
```

---

## Type Checking

Run:

```bash
python -m mypy --package sentinel_x
```

The local `src` directory is configured as the Sentinel-X mypy
source root through `pyproject.toml`.

Sentinel-X core code is checked using strict mypy settings.

---

## Linting

Run:

```bash
ruff check src/sentinel_x tests
```

Automatic formatting is configured but is not yet part of the
quality gate. Formatting enforcement will be enabled after the
existing Phase 0 source files have been normalized in a controlled
migration.

---

## Legacy Baseline

The original linux-sentinel implementation is intentionally retained
during Phase 0.

Legacy files currently include:

```text
main.py
dummy_service.py
requirements.txt

src/monitor.py
src/watchdog.py
src/logger.py
```

They are preserved as an engineering and experimental baseline.

New Sentinel-X development lives under:

```text
src/sentinel_x/
```

---

## Research Roadmap

```text
Phase 0
Engineering baseline

Phase 1
Linux host observability

Phase 2
systemd and journald observability

Phase 3
Fault-injection laboratory

Phase 4
Fault and anomaly detection

Phase 5
Dependency and causal graph construction

Phase 6
Root-cause analysis

Phase 7
Safety-constrained remediation

Phase 8
Post-remediation verification

Phase 9+
Adaptive telemetry, uncertainty estimation,
causal learning, and constrained agentic reasoning
```

---

## Safety Principle

Sentinel-X will not treat arbitrary shell execution as an
intelligence interface.

Future remediation will use explicit, validated, constrained actions
through native Linux mechanisms.

The intended architecture is:

```text
Reasoning
   ↓
Safety Policy
   ↓
Approved Action
   ↓
Controlled Executor
   ↓
Native Linux Mechanism
```

not:

```text
AI
 ↓
arbitrary shell
```

---

## Project Status

Current status:

```text
Phase 0.5A
Engineering Quality Gates
```

Sentinel-X is still a research and development system and should not
yet be used as an autonomous production remediation agent.
