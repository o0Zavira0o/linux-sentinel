# Technical Debt Register

Real known problems are tracked here. They should not all be fixed immediately. Priority follows information gain and roadmap gates.

## TD-001 — Historical README/documentation drift
**Status:** resolved by the documentation bootstrap at commit `f47b5cb764030ea8c78d53ff76ebe0859bb4ca29`.

README remained at Phase 0 while implementation reached Phase 5E.4, causing new sessions to reconstruct the wrong state. Ongoing prevention is enforced by `AGENTS.md` and the documentation Definition of Done.

## TD-002 — Phase-5 dependency public surface is too large
Current dependency research package exports roughly 139 symbols without demonstrated stable external consumers.

**Why not fix now:** FULL must remain intact for 5F ablation.

**Target:** 5R if thesis survives.

## TD-003 — Identity namespace proliferation
Phase 5 introduced many content-addressed identities/fingerprints.

**Target:** evidence-based collapse in 5R.

## TD-004 — Repeated primitive validation
Aware datetime, positive/nonnegative/bounded integers, text, boot IDs, and digest validation repeat across models.

**Target:** shared primitives in 5R.

## TD-005 — Protocol/factory proliferation
Many Protocols exist mainly to substitute one implementation in tests.

**Future policy:** Protocols primarily at real I/O/external boundaries or multiple real implementations.

## TD-006 — Evidence characterization naming
`calibration` is potentially misleading because probability calibration is not performed.

## TD-007 — Incident hard-crash continuity is not durable
Open incident runtime continuity is not crash-safe persistence.

## TD-008 — Detection bridge queues are memory-only
Hard crash can lose pending lifecycle/deferred observation state.

## TD-009 — No real reboot campaign
Boot-boundary logic is heavily unit-tested but broad real reboot campaigns are absent.

## TD-010 — No distro/systemd diversity
Fedora is the primary real validation environment.

## TD-011 — No long-running soak/high-rate benchmark
Missing CPU/RSS/FD/event throughput/duplicate/loss/storage/backlog measurements.

## TD-012 — Lab privilege model is broad
Some proof processes execute under operator-authorized sudo instead of a narrow immutable helper.

## TD-013 — Graph+Time comparison not yet scored
**Status:** implementation portion resolved in Phase 5F.2; evaluation remains open.

A small B1 Graph+Time comparator now exists alongside B0 and B1S, but none has yet been scored on the frozen HARD corpus. Corpus-v1 is frozen by completed 5F.3B; the 5F.5 scorer has passed authoritative Fedora validation, and scoring remains intentionally blocked until its checkpoint is exact-SHA CI-proven and formally frozen. Current Phase-5 synthesis remains unvalidated against the smaller heuristic until later scoring milestones run.

## TD-014A — Corpus-v1 live capture not yet frozen
**Status:** resolved by Phase 5F.3B.

Attempt #4 captured all 16 empirical executions, derived all 20 adversarial cases, passed independent artifact audit, and froze corpus-v1 before scoring. The remaining benchmark gate is the 5F.5 scorer freeze, not corpus acquisition.

## TD-014 — No blinded RAW-vs-structured LLM baseline
The core evidence thesis has no comparative LLM evaluation yet.

## TD-015 — No operator-value study
No experienced-operator study demonstrates time/accuracy benefit.

## TD-016 — General trusted public replay path is incomplete
Archived canonical evidence can be audited, but there is no small stable public replay path for all research artifacts.

## TD-017 — Ground-truth fault modes are narrow
Current lab faults are `SERVICE_INACTIVE` and `SERVICE_FAILED`. Expansion is frozen during 5F.

## TD-018 — Requires/Wants controlled result is largely expected from documented semantics
Further variants would have limited research information. Future work should target topology–evidence divergence if unlocked.
