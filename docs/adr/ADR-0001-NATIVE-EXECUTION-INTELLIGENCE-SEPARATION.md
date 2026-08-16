# ADR-0001 — Native Execution / Intelligence Separation

Status: Accepted
Phase: 0+

## Context
A fault-detection/remediation project can drift into arbitrary shell execution controlled by increasingly complex “intelligence.” That weakens safety and makes behavior difficult to audit.

## Decision
Native Linux mechanisms such as systemd remain the lifecycle execution mechanism. Sentinel-X observes, reasons, applies policy, and verifies above those mechanisms.

## Why
- preserve native Linux semantics;
- constrain the mutation boundary;
- separate evidence/reasoning from execution;
- prevent arbitrary shell from becoming the intelligence interface.

## Alternatives Considered

### Arbitrary shell executor
Rejected because safety, validation, provenance, and blast-radius policy become substantially harder.

### Replace systemd lifecycle behavior in Sentinel-X
Rejected because Sentinel-X should not reimplement the service manager.

## Consequences
Positive: clearer safety/evidence boundaries.
Negative: some capabilities depend on what native mechanisms expose.

## Affected Components
systemd readers, Fault Lab, future remediation.

## Reversal Conditions
Only if concrete evidence shows native execution cannot satisfy required safety/semantic needs and a replacement provides stronger bounded behavior.
