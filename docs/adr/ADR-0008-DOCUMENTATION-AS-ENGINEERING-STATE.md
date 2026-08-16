# ADR-0008 — Documentation as Authoritative Engineering/Handoff State

Status: Accepted / Binding
Date: 2026-08-16
Phase: 5F.0

## Context
Implementation advanced from Phase 0 to Phase 5E.4 while README remained at engineering-baseline state. New conversations/engineers therefore reconstructed stale architecture and roadmap context from chat history.

## Decision
Repository documentation becomes part of engineering state and Definition of Done.

Authoritative separation:

- `PROJECT_SPEC.md` — what the project is meant to become;
- `ARCHITECTURE.md` — architecture that exists now;
- `CURRENT_STATE.md` — current implementation/status snapshot;
- `ROADMAP.md` — future path;
- phase files — phase-specific history/intent;
- ADRs — why architectural decisions were made;
- invariants — what must not accidentally change;
- exceptions — odd-looking but intentional/current behavior;
- technical debt — real problems intentionally deferred;
- `START_HERE.md` — compact bootstrap index.

`AGENTS.md` defines mandatory reading and per-push documentation-update rules.

## Why
The goal is not to preserve conversations. The goal is to preserve enough decision context to reconstruct the project safely without relying on chat memory.

## Rejected Alternative
One giant `CHAT_HISTORY.md`/`FULL_PROJECT_HISTORY.md` is rejected because it recreates the context-interpretation problem and becomes stale/noisy.

## Consequences
Documentation review becomes a freeze step. Contradictions are reported rather than silently reconciled.

## Reversal Conditions
The exact file organization may evolve, but authoritative separation and handoff requirements remain unless a demonstrably better project-state system replaces them.
