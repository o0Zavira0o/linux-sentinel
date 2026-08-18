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

A small B1 Graph+Time comparator now exists alongside B0 and B1S, but none has yet been scored on the frozen HARD corpus. Corpus-v1, the 5F.5 scorer, and the generic blinded execution preflight are frozen; the latter is formally closed by `291fa32cea930b78d933c3dd35e11260c98bab3f` / CI `32108809270`. A pre-score audit found an implementation completeness gap: the frozen scorer row accepts only RAW/MINIMAL/FULL condition labels, so B0/B1/B1S identities cannot be passed directly even though the preregistered B1-vs-B1S HARD collapse rule requires common M1–M7 scoring. The corrective 5F.6 adapter keeps the scorer unchanged, records baseline identity externally, and reuses the actual evidence condition consumed by each deterministic baseline (`minimal` for B0/B1; `full` for B1S). It is frozen at checkpoint `6d86fff7d0699ca4d11d48cdd98538ed81eb0380` with exact-SHA CI run `32123764282`. Concrete provider/model/tokenizer controls and ablation mappings remain unfrozen, and no deterministic corpus score has been inspected. Current Phase-5 synthesis remains unvalidated against the smaller heuristic until the preregistered scoring milestones run.

## TD-014A — Corpus-v1 live capture not yet frozen
**Status:** resolved by Phase 5F.3B.

Attempt #4 captured all 16 empirical executions, derived all 20 adversarial cases, passed independent artifact audit, and froze corpus-v1 before scoring. The 5F.5 scorer, generic 5F.6 execution preflight, and deterministic-baseline common-scoring adapter are frozen (adapter checkpoint `6d86fff7d0699ca4d11d48cdd98538ed81eb0380`, CI `32123764282`); remaining benchmark work is the concrete blinded execution manifest/capacity plan, mandatory ablation mappings, and then the preregistered comparison/ablation run, not corpus acquisition or score-policy definition.

Concrete Execution Freeze has a CI-PROVEN first local provider implementation checkpoint at `991fe365300ed6a507d0e961fda48ed4bad8e566` / run `32155042040`, followed by a valid pre-score capacity falsification and corrective live proof. The paid hosted-API candidate was abandoned before commit/provider request because a usable paid credential was unavailable. The local replacement pins Ollama `0.32.13`, Gemma 4 12B IT QAT Q4_0 GGUF `93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b`, and Ollama model digest `be1d79d105352d8cb0a25ee03f1f315935cc93fb4f0674422c2bb13be72fc025`. The Python 3.14 bodyless-GET defect was corrected and fully regression-tested; model-store permissions remained private; 76/76 synthetic render/tokenizer equivalence remained exact. The frozen 108-request map then showed that the otherwise resource-feasible 8192 context was epistemically inadequate: 56/108 requests exceeded its 5120-token input budget, maximum 38091. That map is preserved at `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2` rather than rewritten. Corrective 49152 + f16 was validated with a 38087-token synthetic long-context request, exact tokenizer/inference equality, 79% GPU placement, 14.208 generation tok/s, peak 6735 MiB GPU memory, and zero swap. Remaining execution debt is repo validation/checkpoint/CI for the 8192→49152 correction, corrected-manifest 108-count rebind, exact 324-attempt plan, formal Concrete Execution Freeze, and mandatory ablation construction.

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
