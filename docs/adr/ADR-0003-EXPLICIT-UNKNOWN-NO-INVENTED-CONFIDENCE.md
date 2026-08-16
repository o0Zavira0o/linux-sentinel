# ADR-0003 — Explicit Unknown and No Invented Confidence

Status: Accepted
Phase: 4+

## Context
Operational systems often force incomplete evidence into a classification or attach confidence-like numbers without calibration.

## Decision
Unknown/transitional/unsafe evidence remains explicit (`UNASSESSED`/insufficient/missing). No numeric probability/confidence is assigned unless a real calibrated method is introduced and validated.

## Consequences
Outputs may be less superficially decisive, but they remain auditable and falsifiable.

## Related
Phase-4 detector, evidence characterization, Phase-5 coverage/synthesis, future Claim Gate.

## Reversal Conditions
Numeric confidence may be introduced only with preregistered calibration methodology and empirical validation appropriate to the claimed population.
