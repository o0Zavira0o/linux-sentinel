# Known Architectural and Implementation Exceptions

This file documents behavior that may look wrong, redundant, premature, or inconsistent but currently exists intentionally. Real deferred problems belong in `TECH_DEBT.md`.

## EXC-001 — Phase-5 complexity is intentionally retained during falsification

**Location:** `src/sentinel_x/dependency/`

**What looks wrong:** many models/identities/validators/exports for a subsystem without a normal CLI consumer.

**Why it exists:** Phase 5F requires a stable FULL representation to compare against MINIMAL evidence. Refactoring first would destroy the experimental control.

**Do not change:** mass-refactor/remove/rename Phase-5 models, identities, protocol layers, or exports before the 5F verdict.

**Removal condition:** Full-vs-Minimal ablation completed.

**Target:** 5R if thesis survives.

## EXC-002 — Phase-5 dependency subsystem has no normal CLI consumer

This is currently a research/controlled-experiment subsystem. 5F tests whether it earns later runtime integration.

## EXC-003 — Incident snapshot contract is not hard-crash durable persistence

`detection/incidents.py` supports snapshot/restore semantics but does not claim crash-safe production continuity.

## EXC-004 — Detection bridge pending/deferred queues are memory-only

`detection/bridge.py` protects in-process ordering/failure semantics. Hard crash may lose unpersisted pending/deferred state.

## EXC-005 — Inactive transition timestamp may be unavailable

Do not fabricate exact inactive transition latency. Missing timing remains missing even when detection succeeds.

## EXC-006 — Controlled lab proofs may run the proof process under sudo

Acceptable for operator-authorized local research; not the target production privilege model and not proof of least privilege.

## EXC-007 — Evidence characterization is currently named calibration

`detection/calibration.py` can sound probabilistic, but current artifacts characterize controlled evidence and explicitly assign no probabilistic confidence.

Do not rename during 5F; consider in 5R if the module survives.

## EXC-008 — Legacy top-level implementation files remain

Historical baseline files are retained but are not current Sentinel-X package architecture/API.

## EXC-009 — Canonical 5E.4 Requires/Wants result validates the harness more than novel systemd semantics

The pair validates relation isolation, evidence capture, mutation/recovery, protocol provenance, and boot/backend binding. Do not continue research by enumerating documented systemd semantics.

Future research direction, if unlocked: Topology–Evidence Divergence.

## EXC-010 — Phase-5E.4 live evidence archive is operator-managed outside source repository

This avoids accidental production-source commits. General trusted public replay remains a future concern only if the surviving architecture needs it.
