# Phase 01 — Linux Host Observability

## Status
FROZEN

## Objective
Create typed Linux-native host evidence paths for contextual system observation.

## Completed Capabilities

- host identity;
- CPU/load;
- memory;
- filesystem;
- disk I/O;
- network;
- process observations and delta sampling;
- runtime collector integration.

## Architectural Intent
Collectors preserve bounded source evidence and do not own anomaly/causal policy.

## Current Strategic Interpretation
Host observability is necessary context but **not a project differentiator**. Feature expansion is frozen during Phase 5F. If future evidence needs standard telemetry available from existing ecosystems, prefer integration rather than collector proliferation.

## Known Risks/Debt
Real cross-environment/performance behavior is not broadly characterized.
