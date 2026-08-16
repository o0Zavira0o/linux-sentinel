# ADR-0007 — Phase 5F Falsification Before Further Architecture Growth

Status: Accepted / Binding
Date: 2026-08-16
Phase: 5F

## Context
An external adversarial audit found strong internal correctness discipline but unproven external value. Phase 5 had grown into a large research subsystem without a normal runtime consumer, while current Requires/Wants results largely validate known systemd behavior and experiment machinery.

## Decision
Freeze Phase 5E.4 intact and stop the previous 5E.5+ feature-growth sequence.

Before further reasoning architecture:

1. preregister thesis/non-goals/corpus/baselines/metrics/kill criteria;
2. compare identical blinded cases under RAW, MINIMAL, and FULL evidence;
3. compare simple deterministic heuristics and blinded LLM reasoners;
4. measure unsupported claims, abstention, provenance, counterevidence, and correctness;
5. ablate FULL vs MINIMAL;
6. issue an explicit A/B/C/D/E verdict;
7. refactor only after measured evidence identifies what matters.

## Binding Thesis

> Sentinel-X exists to make operational diagnoses falsifiable.

## Alternatives Rejected

### Continue 5E.5 repeated-execution feature growth immediately
Rejected because it adds architecture before proving evidence value.

### Immediately delete/refactor Phase 5
Rejected because FULL must remain intact for valid ablation.

### Implement Claim Gate immediately
Rejected because the project must first prove that evidence structure changes reasoning quality.

## Consequences
Phase 5F becomes the final Phase-5 decision gate. Feature expansion is frozen. Architecture reduction becomes evidence-based.

## Reversal Conditions
Only valid Phase-5F evidence or an invalidated benchmark may revise this sequence. Sunk cost is not a reversal argument.
