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

A small B1 Graph+Time comparator exists alongside B0 and B1S, but none has yet been scored on the frozen HARD corpus. Corpus-v1, the 5F.5 scorer, generic blinded execution preflight, and deterministic-baseline common-scoring adapter are frozen. Concrete provider/model/tokenizer/capacity controls are now evidence-complete at corrective checkpoint `29573586877ebcbc475ed2f00172129089e60fdd` / CI `32169631419` with manifest `e0da42efc5e67fc3d86b434a99e3e46dda257ba1a8b049c2adbb408e79582ee6`, blinded count map `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2`, exact 324-plan `0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542`, and binding candidate `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3`. Earlier `ZipFile.testzip()` count harnesses are retained as procedural history but superseded for blinding claims by the clean visible-member-only rebind. Mandatory ablation mappings are now evidence-complete and are being formally frozen by this docs-only transition; no deterministic corpus score has been inspected. Current Phase-5 synthesis remains unvalidated against the smaller heuristic until the preregistered scoring milestones run.

## TD-014A — Corpus-v1 live capture not yet frozen
**Status:** resolved by Phase 5F.3B.

Attempt #4 captured all 16 empirical executions, derived all 20 adversarial cases, passed independent artifact audit, and froze corpus-v1 before scoring. The 5F.5 scorer, generic 5F.6 execution preflight, deterministic-baseline common-scoring adapter, corrected local concrete execution controls, clean blinded 108-count map, and exact 324-attempt plan are now frozen as pre-score evidence. The complete concrete-execution chain remains bound by candidate `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3`. The mandatory ablation mapping chain is now evidence-complete through preregistration, CI-proven implementation, full 252-coordinate realization, and corrected capacity proof. Remaining benchmark work is the exact score-bearing ablation execution-semantics freeze and then preregistered comparison/ablation execution, not corpus acquisition or score-policy definition.

Concrete Execution Freeze progressed from the first local provider checkpoint `991fe365300ed6a507d0e961fda48ed4bad8e566` / CI `32155042040` through a valid pre-score 8192 capacity falsification, corrective live 49152 proof, and the repo-only corrective checkpoint `29573586877ebcbc475ed2f00172129089e60fdd` / exact-SHA CI `32169631419`. The local identity remains Ollama `0.32.13`, Gemma 4 12B IT QAT Q4_0 GGUF `93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b`, model digest `be1d79d105352d8cb0a25ee03f1f315935cc93fb4f0674422c2bb13be72fc025`, context 49152, output 2048, safety 1024, and unchanged isolation/sampling boundaries. The numeric map SHA `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2` continues to show 56/108 over the old 5120-token budget and 0/108 over the corrected 46080-token budget. A procedural audit found that older count harnesses called `ZipFile.testzip()`, which read all ZIP member content for CRC; those attempts are retained but are not the blinding authority. The clean blinded rebind prohibits `testzip()`, opens only RAW/MINIMAL/FULL member content, records zero hidden-member content reads, and reproduces the exact map; evidence `9ef61454beb0e57f4818ba957ba3e01839b5d6ccf632f8097ca662b17655c548`, summary `b954e3c7aed5584493464ef8170116ead7a90586705a12b6cae016ae03cb1951`, receipt `ab985ac420b75add02a3627066e09d2e6a93ff2822301464f303dd3f040fe3d9`. The exact 324-plan is frozen at `0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542` / file `61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80` with deterministic order/repeat identity and zero capacity violations. Binding candidate `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3` / receipt `729367c756e8b7ac35c10ccfd50ac10fd0846face9e2bc1547813077ccf0a806` continues to bind the concrete execution source/CI/resource/corpus/map/plan chain. Mandatory ablation construction is now evidence-complete; this docs-only transition formalizes that mapping freeze while score-bearing execution remains unauthorized. Remaining execution debt is the exact score-bearing ablation execution-semantics freeze.

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
