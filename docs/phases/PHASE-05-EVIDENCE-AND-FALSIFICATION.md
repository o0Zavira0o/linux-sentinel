# Phase 05 — Dependency Evidence, Propagation Research, and Falsification

## Status

- 5A–5E.4: **FROZEN reference**
- 5F: **ACTIVE**
- 5R: conditional

Frozen implementation baseline:
`1733485fdce630e4a3c32731c7dcbc62cbdebefb`

## Why Phase 5 Initially Existed
Move from “what service is broken?” toward dependency-aware evidence about possible downstream effects without silently upgrading topology/correlation into causality.

## 5A — Typed systemd Dependency Evidence
Status: FROZEN

Relations:
- `Requires`
- `Wants`
- `After`
- `Before`

Requirement and ordering remain distinct; evidence is explicitly noncausal.

Commit: `190b9c82b2b84e159034ea868c353b110b605647`.

## 5B — Bounded Dependency Discovery
Status: FROZEN after parser correction

Traversal follows requirement relations and retains ordering as context.

Original: `1644eb073d47b415406542c9885593942f28d14d`
Corrective parser/evidence-ingestion: `ecf384bbabf78df93c6960d42207fc17795049c0`

## 5C — Versioned Dependency Graph
Status: FROZEN

Separates topology identity from graph/evidence version.

Commit: `1b75891ceded163fe3763cfa68f93a8335ebe084`

## 5D.1 — Temporal Propagation Evidence
Status: FROZEN after temporal-basis correction

Distinguishes state-change timing, assessment-time fallback, forward/outside/simultaneous/reverse/limited findings, and counterevidence vs insufficient timing.

Commit: `4c97dd95354d442670dadef715c7e528806ad27d`

## 5D.2 — Sampling Coverage / Negative Evidence
Status: FROZEN

Core rule:

> no anomaly observed under sparse/no sampling is not “no propagation.”

Commit: `c140fde3ed69444054ec9d85036ec15fbb131167`

## 5D.3A — Controlled Pair Experiment Contracts
Status: FROZEN

Deterministic source/dependent fixtures, Requires/Wants contrast, fixed `After=source`, intervention ground truth, conservative evidence classes.

Commit: `7475bdbaebc28685effd4afec5907b57f572a319`

## 5D.3B — Controlled Live Runner
Status: FROZEN

Actual manager verification, baseline health, concurrent dependent sampling, source fault, recovery, coverage, experiment evidence.

Commit: `71f7c421937fc580b28646be0d26c7438e94aa93`

Historical canonical pattern:
- Requires -> affected anomaly observed
- Wants -> bounded negative evidence

Not a universal causal/systemd claim.

## 5E.1 — Candidate-Local Conservative Evidence Synthesis
Status: FROZEN

Commit: `ba75f63253d8e7be21c43105148b3ce7b27e8c9f`

## 5E.2 — Typed Controlled Paired Contrast
Status: FROZEN

Commit: `67306b47c62dcf9bc91ad006b8e42b857a9f7e00`

## 5E.3 — Typed Pre-Execution Protocol Provenance
Status: FROZEN

Commit: `8dea56a4a99956246234b1a2cd91cddc8040c8ca`

## 5E.4 — Protocol-Bound Controlled Live Execution
Status: FROZEN

Commit: `1733485fdce630e4a3c32731c7dcbc62cbdebefb`

Binds intended protocol, boot stability, explicit Sentinel-X backend profile, execution attempt, actual live runner, and exact result binding.

An audit closed a boot-binding TOCTOU before live mutation.

Canonical live proof:

```text
boot = 4ff955e4421e487b946423c40beb713c
backend_profile = backendprof-89422c6008ed5c7636d058ea2c672fb34d0645189ebe3c1270056eea529754b9
Requires evidence = affected_anomaly_observed, samples=20
Wants evidence = bounded_negative_evidence, samples=20
paired profile = requires_anomaly_wants_bounded_negative
canonical JSON SHA-256 = 295e8bc66b65ad8f56ad5d2a2ab74fcf1c5ab37e7c8e45d07340ce0b06b002b2
```

## Strategic Reset

Adversarial audit verdict:

```text
INTERESTING INFRASTRUCTURE, UNPROVEN VALUE
GOOD THESIS CANDIDATE, OVER-ENGINEERED IMPLEMENTATION
```

Binding decision:

> Stop Phase-5 feature growth. Keep FULL reference intact. Attempt to falsify the thesis before more architecture.

New thesis:

> **Sentinel-X exists to make operational diagnoses falsifiable.**

## 5F — Falsification (ACTIVE)

Official objective:

> Falsify or validate the claim that Sentinel-X structured evidence materially improves operational reasoning over simpler alternatives.

### 5F.0 — Freeze & Pre-registration
Status: **FROZEN**.

Required:
- `research/phase5f/THESIS.md`
- `NON_GOALS.md`
- `EVALUATION_PROTOCOL.md`
- `BASELINES.md`
- `METRICS.md`
- `KILL_CRITERIA.md`

No scored benchmark result is inspected before freeze. The preregistration documents are now the fixed benchmark contract unless the benchmark itself is invalidated and restarted under a new version.

Documentation note: the six preregistration files were substantively frozen at `f47b5cb764030ea8c78d53ff76ebe0859bb4ca29`. During 5F.1 their stale `PRE-REGISTERED DRAFT` header was corrected to `FROZEN`; no thesis, baseline, corpus, metric, threshold, or kill criterion content changed.

### 5F.1 — Evaluation Projection
Status: **FROZEN**.

Private `_phase5f` boundary now produces RAW / MINIMAL / FULL views from one immutable gold-free `CaseSource`, preserves opaque evidence references, restricts MINIMAL to preregistered factual categories, and keeps hidden `CaseGold` out of package-root visible exports. No frozen Phase-5 evidence model or public dependency API changed.

### 5F.2 — Baselines
Status: **FROZEN**.

Implemented function-only private comparators without changing frozen `dependency/`: B0 maps dependent service state while intentionally ignoring topology/timing/provenance/coverage; B1 uses MINIMAL factual scope, requirement reachability, monotonic source/target transitions, boot compatibility, ambiguity, and bounded healthy coverage; B1S validates current synthesis schema/count/interpretation claim boundaries and maps it conservatively to common benchmark labels. Forward temporal consistency alone remains `INSUFFICIENT`, not an observed effect. No scored comparison or value claim is assigned by 5F.2.

### 5F.3 — Blind Corpus
Status: **FROZEN**.

**5F.3A — Corpus Contract & Capture Harness: THIRD CORRECTIVE CHECKPOINT FROZEN at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`.** Private corpus modules keep the exact 36 opaque case IDs, 16-run empirical execution order, hidden `source_run_group`, same-case lineage audit, separated visible/hidden export, and five deterministic adversarial transformations. The first 5F.3B live attempt using `07e19d5dbb39f831a69e4b41b60624ec786558f8` failed before corpus export because the RAW sidecar was stopped immediately after the frozen runner returned and could miss the final controlled-fault boundary by less than one sampling interval. This was a harness correctness defect, not evidence disagreement: the existing validator correctly rejected the case. The corrective harness now waits, under an explicit timeout, for a completed sidecar round at or after the closed ground-truth fault end before stopping the sidecar. No reasoner was run, no score was computed, and the failed attempt is not corpus-v1.

**5F.3B — Live Corpus Capture & Freeze: FROZEN.** The second attempt on `8638bc033d6010c0d98aec67802cb24f75def864` exposed the reverse/counterevidence live-shape assumption. The third attempt on `7f405633d104bc1360a10d066dd75ebb8d027fbe` exposed the multiple-candidate hidden-gold assumption. Both failures were retained as research evidence and neither produced a valid frozen corpus or score. After the third corrective implementation was frozen at `ff0faa29dc0378263e84dcfbe74f5d46067a7f38`, repository checkpoint `64cffbe48c4a919752d68edae19f6a763295eb6e` became the CI-proven live-capture authority. Fresh attempt #4 restarted all 16 empirical executions, derived all 20 adversarial cases, passed the internal corpus audit, passed a separate exported-artifact audit, and froze the exact archive before scoring. Corpus-v1 contains 36 cases / 16 empirical / 20 adversarial / 28 HARD; capture boot `4ff955e4421e487b946423c40beb713c`; archive SHA-256 `a31876f660d61f3bb9d14f181c6da78568f67fa05631b264804e589189886e18`; preserved attempt-04 log SHA-256 `5391a3014643c6bfc352c46e60b32b98ebb1a1985587531b641ff5c7913a1128`; `scored_reasoner_outputs_present=False`. The frozen preservation set is operator-local, hash-verified, read-only, and outside the repository. An initial external Gate-3 audit command used the wrong spelling for the frozen observer-pilot statistical-disclaimer key; a read-only root-cause check proved the artifact correct and the corrected full audit passed without recapture or artifact mutation.

Execution protocol: [`../../research/phase5f/CORPUS_V1_CAPTURE.md`](../../research/phase5f/CORPUS_V1_CAPTURE.md). The protocol file is retained unchanged after the successful campaign; its historical corrective-status header is not a current milestone indicator and is intentionally not rewritten post hoc.

### 5F.4 — Structured Reasoner Output
Status: **FROZEN at `3604fb3ed5571ee02f5a6e448628ae68bc201f73`**.

The minimal private implementation performs strict bare-JSON parsing and exact validation for the preregistered final output: classification, independent abstention, claims with causal strength/evidence references/text, unresolved items, and top-level evidence references. It rejects duplicate JSON keys, non-standard constants, prose/code fences, schema extras, malformed/duplicate reference syntax, and invalid causal-strength labels without importing hidden gold or scoring policy. It deliberately does not infer abstention from classification, require citations on claims, or reject well-formed references merely because they are unsupported/wrong-scope; those remain independently measurable failures for later frozen scoring. Frozen B0/B1/B1S behavior is untouched. Fedora validation, exact repository/stage guards, commit/push, local/remote equality, clean-repository verification, and four-job exact-SHA CI run `32059127141` all passed. No scored B2/B3/B4 output exists.

### 5F.5 — Primary Metrics
Status: **FROZEN at implementation checkpoint `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`; exact-SHA CI run `32062765897` PASS; formal freeze/5F.6 activation commit `5d8c1e227894a88a1f46de715b506903ceb58045` with exact-SHA CI run `32105743211` PASS**.

The private scorer implements M1–M8 and the preregistered corpus analysis slices without modifying the six frozen preregistration files. It validates the exact 324-attempt LLM matrix and preserves `source_run_group` visibility so adversarial derivatives are never silently presented as independent empirical runs.

Before any scored output exists, the implementation pins the otherwise machine-necessary conventions without changing metric thresholds: parse failures are not repaired and create no fake claim/reference denominators; missing classifications are incorrect and contribute only the gold-class false negative for M2; missing abstention is no positive abstention prediction; M3 treats non-`none` causal strength as a causal claim and applies the frozen strength order; M5/M6 count claim-level reference uses, with support-set membership defining M5 validity and invalid-set membership defining M6 provenance violations; M7 is preserved by explicit counterevidence reference or exact hidden-gold-compatible classification; M8 uses three runs and parse failures never form a classification mode. Empty natural denominators remain missing. Secondary slices with a completely absent classification report that per-class F1 as missing and average only defined classes; HARD/full contain all five labels and therefore retain the exact five-class primary Macro-F1.

This is a pre-score implementation interpretation, not a rewrite of `METRICS.md`, `EVALUATION_PROTOCOL.md`, thresholds, gold, corpus, or kill criteria. Authoritative Fedora validation passed 15/15 dedicated 5F.5 tests, the 89/89 private Phase-5F regression, documentation integrity, and the full 1337/1337 repository gate with 156 Python files formatted and strict mypy clean across 84 source files. Exact repository/stage guards, checkpoint `8f59bf04ae1e9e755cc6a7f7e0662d8be8d984c5`, push/local-remote equality, clean-repository verification, and four-job exact-SHA CI run `32062765897` all passed. No scored B2/B3/B4 output exists.

### 5F.6 — Ablation
Status: **ACTIVE — Concrete Execution Freeze and Formal Mandatory Ablation Mapping Freeze are closed; the private mandatory-ablation scorer-side adapter is CI-PROVEN at `07330c4d68d149075ce0c906e4f50e046d9221b1` / exact-SHA CI `32262977658`; this docs-only checkpoint is the Formal Ablation Scoring Adapter Freeze transition conditional on green exact-SHA CI; external score-bearing ablation execution remains unfrozen and unauthorized; no benchmark score-bearing request or benchmark score exists**.

The frozen generic preflight is a private function-only boundary that performs no provider call, scoring, or hidden-gold access. It pins the common prompt/output-schema digests and frozen prerequisite identities; validates an explicit provider/model identifier/version/configuration/tokenizer/context manifest; requires fresh stateless no-tool/no-web requests; fixes provider/transport failure handling to at most one retry of the exact semantic request; keeps condition/repeat control metadata outside the reasoner-visible request; deterministically plans exactly 324 B2/B3/B4 attempts; and rejects any plan whose externally measured 108 semantic-request token counts do not fit the pinned context/output/safety budget without truncation. Invalid structured output is not transport-retried and remains unrepaired for the frozen 5F.4/5F.5 path. Authoritative Fedora validation, exact stage/repository guards, checkpoint `39d1db2845f6d461ab21a6da2077be2b77bf5982`, push/local-remote equality, clean-repository verification, and exact-SHA four-job CI run `32108226447` all passed.

The generic boundary leaves provider/model open. The deterministic B0/B1/B1S common-scoring adapter is frozen without reopening the scorer. Concrete Execution Freeze instantiates the provider slot locally as Ollama `0.32.13` with Gemma 4 12B IT QAT Q4_0, exact GGUF SHA `93567e57a8fe10b23569b9d9ec38cd005deedf71e29477c421a4b83f418a538b`, and Ollama model digest `be1d79d105352d8cb0a25ee03f1f315935cc93fb4f0674422c2bb13be72fc025`. The service remains loopback-only, cloud-disabled, single-parallel, single-loaded-model; future score payloads keep thinking/truncation/context shifting off, no tools/web/format repair, temperature 1.0/top-p 0.95/top-k 64, three repeat seeds, f16 KV, and `keep_alive=0`; normal score-bearing generation transport remains absent. The first local checkpoint `991fe365300ed6a507d0e961fda48ed4bad8e566` / CI `32155042040` used context 8192. Its preserved token-count map SHA `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2` validly falsified the 5120-token input budget at 56/108 over budget (32 RAW, 24 FULL, 0 MINIMAL), maximum `CASE-0028:raw = 38091`. A later methodological audit found that the earlier token-count harnesses called `ZipFile.testzip()`, which reads every ZIP member content stream for CRC. Those artifacts remain numeric/procedural history and their count map was later reproduced exactly, but their hidden-member-content-read claim is not authoritative. Corrective context 49152 was selected before any benchmark score while output 2048 and safety 1024 remained unchanged, yielding a 46080-token input budget. Short f16 proof was CONDITIONAL PASS at 79% GPU/zero swap; the frozen 38087-token synthetic long-context run matched tokenizer and inference counts exactly and passed at 14.208 generation tok/s, 79% GPU, peak 6735 MiB, zero swap. The repo-only correction passed Fedora validation and exact-SHA CI at `29573586877ebcbc475ed2f00172129089e60fdd` / `32169631419`, yielding manifest `e0da42efc5e67fc3d86b434a99e3e46dda257ba1a8b049c2adbb408e79582ee6`. The authoritative clean blinded rebind prohibits `testzip()`, opens member content only from RAW/MINIMAL/FULL, records zero hidden/nonvisible member content reads, reproduces map `a2ddadf4fb93ad66589277122cdc40091f61ca488ba0f0be5f3f3de03f649ab2`, re-demonstrates 56/108 legacy violations, and yields 0/108 corrected violations; evidence `9ef61454beb0e57f4818ba957ba3e01839b5d6ccf632f8097ca662b17655c548`, summary `b954e3c7aed5584493464ef8170116ead7a90586705a12b6cae016ae03cb1951`, receipt `ab985ac420b75add02a3627066e09d2e6a93ff2822301464f303dd3f040fe3d9`. The exact deterministic 324-attempt plan is frozen at semantic SHA `0f9598d1ba9329b8de70339664c75982af3c3cac8111c29c46529204e8932542` / file SHA `61b00ea75f7183f0bee6828b06d77125037cf308f073669906391729e4bbdf80` with 324 unique IDs, 108 attempts per condition and repeat, deterministic order verification, repeat semantic identity, and zero capacity/hidden/provider/scoring activity. The whole concrete-execution source/CI/resource/corpus/map/plan chain remains bound by candidate `f87f7ff315b5a2d3cc310c8428a7822ac7ffd4c9a27a6ad408f4524f1fc790a3` / receipt `729367c756e8b7ac35c10ccfd50ac10fd0846face9e2bc1547813077ccf0a806`.
Mandatory ablation mappings are now evidence-complete before scoring: the seven field-level mappings are preregistered at SHA-256 `5a4a7188742788954c2d7b1d11085057ab56594327c8306305286d9ba1b41844` (receipt `07df5842df8d3f9e9f4f49156068daa15e8689b28895547b39429a69e4493054`); the private deletion-only implementation is CI-proven at `ea137456c97266c3c853a546e5af288b77008867` / exact-SHA CI run `32228043568`; the 252-coordinate visible-only realization is frozen at `2d6afb218e01f14e0e3ac46711a60a1816dad0688d0365b3bc24c4865100f490` (summary `e1764bcb93042f1ad7ff7a3f98306c679a99d8c52c8f1a4455d4d7581a606165`, receipt `81a8b03fdd59003a118d2d1016f293fd8856ea1571876050c980c85618fe3205`); and the corrected 252-request render/token capacity audit is frozen at count-map `e5289d08fc2387e9fc1ad5a6c47c663f79ed4ef5387cbd8f43abf3cb8b2937cb`, evidence `7e7d358b24a9f386b27e981d51f958f5b72d05d06eb74089a6210d91a4f48cd6`, summary `803ca78c23702baced9261b86ccf41124185faf65cbd9686ad64812d1af6e2b2`, and receipt `921552ec7a711d583b8098a41cf482b771936915ca3f2ad698d47192ef674531`.
All 252 ablation prompts fit the frozen 49152 context / 2048 output / 1024 safety contract: maximum measured input is 11369 tokens against the 46080-token limit, with 0/252 over budget. The 32 unchanged counterevidence views match their frozen MINIMAL token counts exactly. The first token-capacity attempt is preserved as a pre-count harness canonicalization failure (failure SHA-256 `7cee6464f22ba4c33b7cf95276e9a490837bf2a87aecedca46af857c6ce4bf4f`); it was not a capacity falsification.
The Formal Mandatory Ablation Mapping Freeze is closed at repository checkpoint `114a397eaf341cd044804220ed00a84fb0c8c70e`; `mandatory_ablation_mappings_frozen=True`. The private scorer-side mandatory-ablation adapter is CI-proven at `07330c4d68d149075ce0c906e4f50e046d9221b1` / exact-SHA CI run `32262977658`. It leaves frozen `scoring.py` and `ablation.py` unchanged, pins scorer-side execution semantics at SHA-256 `d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d`, requires exactly 756 already-captured outputs (7 mappings × 36 cases × 3 repeats), routes six mappings through frozen `minimal` and one through `full`, keeps mapping identity outside frozen scorer rows, and computes M8 independently per mapping. Authoritative Fedora validation passed 21/21 dedicated adapter tests, 176/176 private Phase-5F tests, and 1424/1424 full unit tests with 166 Python files in format/lint scope and strict mypy across 89 source files. This documentation-only changeset is the Formal Ablation Scoring Adapter Freeze transition and becomes authoritative only after its own green exact-SHA CI; `ablation_scoring_adapter_frozen=True` only at that boundary. `scored_execution_authorized=False` remains mandatory. Before any benchmark score-bearing request, the exact external ablation execution semantics (request matrix/repeats/order/seeds, retry/output-capture behavior, and external execution identity) must be frozen separately without reopening the mappings, scorer, scoring adapter, thresholds, corpus, or FULL reference.
These controls operationalize the frozen evaluation protocol; they do not change the six preregistration files, score semantics, thresholds, corpus, FULL reference, or kill criteria.

### 5F.7 — Decision
Must choose A/B/C/D/E verdict.

## 5R — Conditional Reduction
Only if the evidence thesis survives.

Reduction targets:
- shared evidence primitives;
- identity collapse;
- validator collapse;
- public API collapse;
- Protocol reduction;
- smaller synthesis operations;
- `ExperimentSpec` + `ExecutionRecord`-style core;
- calibration naming correction.

## Must Not Be Refactored Yet
Until 5F decision:
- current dependency models/protocol/synthesis hierarchy;
- FULL evidence fields;
- identity names used by FULL;
- live experiment semantics needed to reconstruct reference cases.

## Phase Exit Criteria
Phase 5 ends only after Phase-5F verdict. If thesis survives, 5R completes before Phase 6. If thesis fails, pivot/stop occurs before Claim Gate implementation.
