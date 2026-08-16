# ADR-0002 — Prefer At-Least-Once Evidence Over Silent Loss

Status: Accepted
Phase: 2+

## Context
Exactly-once behavior can be falsely simulated by dropping evidence during publication/checkpoint failures. For diagnostic/research evidence, silent loss is often worse than duplicates.

## Decision
Where source/delivery paths require it, prefer explicit at-least-once semantics: retain/retry exact evidence and tolerate duplicate handling downstream rather than silently discard.

## Examples
- journald cursor/checkpoint continuity;
- derived EventBus bridge pending outboxes.

## Consequences
Consumers must handle identity/idempotency. The system does not claim exactly-once delivery.

## Reversal Conditions
Only if a source/backend can provide verifiable stronger delivery guarantees without silent-loss risk.
