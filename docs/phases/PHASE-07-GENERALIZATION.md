# Phase 07 — Generalization and Topology–Evidence Divergence

## Status
LOCKED / PLANNED

## Entry Prerequisite
Phase 6 demonstrates measurable value.

## Research Objective
Move beyond simple documented systemd relation experiments and measure divergence between declared topology and observed runtime behavior.

## Priority Scenario Families
- declared dependency -> no runtime effect;
- runtime effect -> no declared dependency;
- multi-hop effects;
- hidden socket/file/network dependencies;
- restart-policy-dependent propagation;
- competing causes;
- partial observations;
- topology changes during incidents;
- failure vs recovery propagation asymmetry;
- non-stable repeated interventions.

## Ecosystem Strategy
Prefer adapters:
- OpenTelemetry evidence ingestion;
- standard metric/Prometheus ingestion;
- external chaos backends;
- external benchmark adapters.

Do not create collector/fault proliferation as a substitute for a validated research question.
