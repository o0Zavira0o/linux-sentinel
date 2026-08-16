# Phase 02 — systemd / journald Evidence and Correlation

## Status
FROZEN

## Objective
Create the primary Linux-native service/event evidence layer with explicit delivery and temporal-correlation semantics.

## 2.1 — systemd Service State

```text
systemd
  ↓
safe bounded reader
  ↓
typed snapshot
  ↓
SentinelEvent
```

Unknown/raw manager state is not silently forced into a detector class.

## 2.2 — journald Evidence

Implemented:

- bounded read-only observation;
- acknowledged cursor transaction semantics;
- config/registry/CLI wiring;
- restart-safe atomic checkpointing/recovery.

Core rule:

> do not blindly reset a stale journal cursor.

Delivery is at-least-once where appropriate: duplicate evidence is preferable to silent loss.

## 2.3 — Temporal systemd/journal Correlation

Implemented:

- deterministic pairwise correlation;
- bounded stateful tracking;
- live EventBus integration.

Evidence quality distinguishes exact invocation match from temporal fallback.

## Live Integration Constraint
Correlation and detection bridges share the EventBus. Derived events must not recursively force each other's pending outboxes.

## Representative Historical Commits

- `b8a9364` — deterministic systemd/journal correlation
- `2364c47` — bounded stateful correlation tracking
- `25872ae` — live correlation event wiring

## Future Dependencies
Current/future reasoning relies on boot-bound service/journal provenance and correlation context.
