# Phase 00 — Engineering Baseline

## Status
FROZEN

## Objective
Transform the original informal `linux-sentinel` prototype into a typed, testable, auditable Python engineering baseline capable of supporting later Linux systems research.

## Why This Phase Existed
Later fault reasoning would be meaningless if configuration, runtime execution, event delivery, persistence, quality gates, and CI were unstable.

## Completed Work

- `src/sentinel_x` package structure;
- typed configuration and loader;
- immutable event models/schema versions/run IDs;
- EventBus with subscriber failure isolation;
- lifecycle/runtime/scheduling/execution/registry foundations;
- JSONL evidence persistence and private file permissions;
- CLI/environment doctor/config checking;
- strict mypy;
- Ruff;
- unittest baseline;
- GitHub Actions quality workflow.

## Architectural Intent

```text
Linux mechanisms / collectors
        ↓
typed evidence/runtime
        ↓
future interpretation/policy
```

Avoid arbitrary shell execution as an intelligence interface.

## Important Decisions

- shared EventBus rather than feature-specific buses;
- explicit event identity/schema/run context;
- strict typing as a correctness tool;
- `scripts/check.sh` as common local/CI gate.

## Things That Must Not Be Refactored Casually
Core EventBus/runtime publication/failure semantics.

## Completion Summary
Phase 0 created the engineering substrate; it was not itself the research differentiator.
