# ADR-0006 — Preserve Intended Protocol vs Realized Execution Provenance

Status: Accepted / frozen reference pending Phase-5F ablation
Phase: 5E.3–5E.4

## Context
A controlled experiment can be described one way but executed under a different boot/backend/policy/context. Comparing results without binding intended semantics to execution creates provenance ambiguity.

## Decision
Capture typed pre-execution protocol provenance and bind it to realized controlled live execution, including relevant boot/backend/execution context, while refusing to label content-addressed identity as authentication/cryptographic attestation.

## Consequences
Strong experiment auditability, but substantial model/identity complexity was introduced.

## Strategic Note
Phase 5F tests whether FULL provenance machinery adds independent reasoning value over MINIMAL evidence. Do not defend this architecture based solely on sunk implementation cost.

## Reversal Conditions
If FULL ≈ MINIMAL, Phase 5R should collapse/internalize unnecessary protocol/identity layers while preserving measured value.
