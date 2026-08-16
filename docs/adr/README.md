# Architecture Decision Records

ADRs record **why** important architecture decisions were made. Git history records what changed; ADRs preserve decision context, rejected alternatives, consequences, and reversal conditions.

## Index

| ADR | Decision | Status |
|---|---|---|
| [ADR-0001](ADR-0001-NATIVE-EXECUTION-INTELLIGENCE-SEPARATION.md) | Native Linux/systemd executes; Sentinel-X reasons | Accepted |
| [ADR-0002](ADR-0002-AT-LEAST-ONCE-EVIDENCE-DELIVERY.md) | Prefer at-least-once evidence over silent loss | Accepted |
| [ADR-0003](ADR-0003-EXPLICIT-UNKNOWN-NO-INVENTED-CONFIDENCE.md) | Preserve unknown/missing; no invented confidence | Accepted |
| [ADR-0004](ADR-0004-CONTROLLED-FAULT-GROUND-TRUTH.md) | Real controlled fault ground truth | Accepted |
| [ADR-0005](ADR-0005-DEPENDENCY-EVIDENCE-IS-NONCAUSAL.md) | Dependency/topology evidence is not causal proof | Accepted |
| [ADR-0006](ADR-0006-PROTOCOL-PROVENANCE-REFERENCE.md) | Preserve pre-execution vs realized execution provenance | Accepted / frozen reference |
| [ADR-0007](ADR-0007-PHASE5-FALSIFICATION-BEFORE-GROWTH.md) | Falsification before more Phase-5 growth | Accepted / binding |
| [ADR-0008](ADR-0008-DOCUMENTATION-AS-ENGINEERING-STATE.md) | Documentation is authoritative project handoff state | Accepted / binding |

## ADR Policy

Create/update an ADR when a change materially affects:

- architecture boundaries;
- persistence/state ownership;
- evidence/claim semantics;
- concurrency/delivery semantics;
- public API policy;
- safety/privilege model;
- strategic roadmap/phase sequencing.

Do not create ADRs for ordinary local implementation details.
