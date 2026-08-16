# Phase 03 — Fault Injection / Ground Truth Lab

## Status
FROZEN

## Objective
Create real Linux/systemd controlled ground truth so detection/reasoning claims are not evaluated only against synthetic Python objects.

## Completed Work

- deterministic Sentinel-owned hardened systemd fixtures;
- private staging and exact artifact verification;
- allowed runtime installation path;
- `sentinel-x-lab-*` target restriction;
- `SERVICE_INACTIVE` controlled stop;
- `SERVICE_FAILED` process abort/kill path;
- monotonic ground-truth windows;
- healthy baseline/fault/recovery observation;
- recovery verification;
- reproducible dataset artifacts/hashes/permissions.

## Safety Boundary
Never mutate arbitrary user/production services in the controlled lab path.

## Reproducible Dataset

- dataset ID: `dataset-06b67b5858f34b44ba726d52565777c2`
- records SHA-256: `c51d1daef13f4a319216844d52dc8f933b5c31fbc5c3c05e2e7aaaf31feb54e8`
- report SHA-256: `c78ed60392eb03bf2ad2b7b10af445aeec028e353364a034f5d7a309ba49c405`

## Current Strategic Interpretation
Fault Lab is a valuable ground-truth reference backend, not a reason to rebuild broad chaos tooling. Fault expansion is frozen through 5F; broader faults should later prefer adapters if justified.

## Known Compromise
Some operator live proofs use broad sudo context; narrow production privilege separation is deferred.
