# Phase 06 — Evidence-Constrained Reasoning

## Status
LOCKED / PLANNED

## Entry Prerequisites
- Phase 5F thesis survives;
- Phase 5R reduction completes;
- surviving evidence primitives stabilize.

## Objective
Turn evidence philosophy into a reasoner-agnostic capability: a reasoner may propose claims, but accepted diagnosis must pass a machine-verifiable Claim Gate.

## Planned Minimal Deliverables
- reasoner input view;
- `ClaimDraft` schema;
- Claim Gate;
- Evidence-Carrying / Verified Diagnosis;
- deterministic heuristic reasoner;
- one external LLM adapter for evaluation without vendor lock-in;
- audit trail.

## Planned Claim Semantics
Keep initial kinds small:
- observational;
- structural;
- temporal;
- interventional;
- causal hypothesis;
- bounded negative.

Initial epistemic statuses remain qualitative, not probabilistic.

## Core Rule
Exploration/reasoning output is not accepted operational truth until verified against scope, missingness, counterevidence, and allowed claim strength.

## Out of Scope
- autonomous remediation;
- numeric confidence;
- generic RCA ranking;
- general tracing implementation.
