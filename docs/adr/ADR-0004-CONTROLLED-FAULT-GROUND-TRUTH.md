# ADR-0004 — Controlled Fault Ground Truth

Status: Accepted
Phase: 3+

## Context
Detector/reasoner validation against only synthetic model objects cannot establish that real Linux transitions were observed correctly.

## Decision
Use Sentinel-owned hardened systemd fixtures and actual controlled interventions to create known monotonic ground-truth windows and verify recovery.

## Safety Constraints
- `sentinel-x-lab-*` only;
- canonical volatile runtime path;
- exact artifact verification;
- recovery required;
- never infer ground truth from detector output.

## Consequences
Real evidence is stronger but experiments require privileges and careful cleanup.

## Reversal Conditions
The reference backend may change or expand through adapters, but ground truth must remain tied to real controlled interventions and explicit scope.
