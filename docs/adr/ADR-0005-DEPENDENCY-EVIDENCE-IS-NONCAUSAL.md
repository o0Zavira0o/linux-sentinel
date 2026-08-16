# ADR-0005 — Dependency / Topology Evidence Is Noncausal

Status: Accepted
Phase: 5A+

## Context
systemd manager dependency relations describe configuration/ordering semantics, but an edge alone does not prove an observed runtime effect or cause.

## Decision
Represent dependency relations as structural evidence. Keep requirement (`Requires`/`Wants`) distinct from ordering (`After`/`Before`). Runtime propagation/effect requires separate temporal/coverage/interventional evidence.

## Consequences
Topology can support a hypothesis/candidate but cannot by itself establish propagation or root cause.

## Future Direction
Phase 7 focuses on topology–evidence divergence rather than topology enumeration.

## Reversal Conditions
Only if a future evidence source defines a different formally validated semantics; declarative topology still cannot silently become empirical effect.
