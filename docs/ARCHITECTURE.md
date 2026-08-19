# Current System Architecture

This document describes the architecture that **exists now**: the frozen Phase-5E.4 FULL reference plus the private Phase-5F evaluation boundary added for falsification. Future target architecture belongs in `ROADMAP.md`.

## Architecture Overview

Sentinel-X currently contains three partially overlapping worlds:

1. an operational Linux observation/detection runtime used by the CLI;
2. a Phase-5 dependency/propagation **research subsystem** used by tests and controlled experiment/proof workflows rather than by the normal CLI decision path;
3. a private Phase-5F **falsification/evaluation boundary** that owns blinded RAW/MINIMAL/FULL projection, corpus handling, reasoner-output/scoring adapters, pre-score execution controls, mandatory ablation construction, and scorer-side ablation aggregation while keeping hidden gold outside every reasoner-visible path.

```text
Linux / proc / filesystem / systemd / journald
                 |
                 v
        bounded native readers
                 |
                 v
           SentinelEvent
                 |
                 v
            shared EventBus
        +--------+---------+
        |                  |
        v                  v
 correlation bridge   detection bridge
        |                  |
        v                  v
 derived evidence     incident lifecycle
        |                  |
        +--------+---------+
                 |
                 v
           JSONL/subscribers

Controlled research path:
Fault Lab + systemd manager evidence
                 |
                 v
 dependency discovery / graph
                 |
                 v
 temporal + coverage evidence
                 |
                 v
 controlled propagation experiment
                 |
                 v
 synthesis / paired synthesis
                 |
                 v
 protocol provenance / bound execution

Phase-5F evaluation path:
visible CaseSource
        |
   +----+----+
   |    |    |
  RAW MINIMAL FULL
   |    |    |
   +----+----+
        |
 blinded evidence bundle

hidden CaseGold -> scorer-side evaluation path only
```

## Repository Structure

```text
src/sentinel_x/
  core/            runtime/event/scheduling primitives
  config/          strict configuration models/loading
  observability/   Linux host readers/samplers
  storage/         JSONL event persistence
  systemd/         systemd/journald readers, models, correlation
  lab/             controlled systemd fault fixtures/injection/datasets
  detection/       service detector, incidents, evaluation/benchmarking
  dependency/      Phase-5 dependency/propagation research subsystem
  _phase5f/         private falsification/evaluation support; not public API
  cli.py           current CLI/runtime assembly
```

## Major Modules

### `core`

**Purpose:** shared runtime primitives.

**Owns:**
- `SentinelEvent` model and event semantics;
- lifecycle state;
- EventBus publication/failure reports;
- collector registry/scheduling/execution/runtime orchestration.

**Must not own:** Linux-source parsing or fault-specific detection policy.

**Consumers:** CLI, observability collectors, systemd/correlation/detection bridges, storage.

### `config`

**Purpose:** strict TOML configuration contracts.

**Owns:** validated runtime configuration and loading.

**Must not own:** runtime policy or source parsing.

### `observability`

**Purpose:** bounded Linux host evidence acquisition.

**Owns:** host identity, CPU/load, memory, filesystem, disk I/O, network, process observation/sampling.

**Must not own:** detection/causal policy.

**Current status:** operational foundation, feature expansion frozen during Phase 5F.

### `storage`

**Purpose:** private JSONL event persistence.

**Owns:** per-run structured append behavior and storage failure contracts.

**Must not own:** interpretation.

### `systemd`

**Purpose:** native systemd/journald evidence acquisition and correlation.

**Owns:**
- service-state models/readers/observations;
- boot identity normalization;
- journal models/readers/checkpointing/binding;
- systemd/journal correlation and EventBus bridge;
- shared systemctl string-array codec.

**Key boundary:** raw manager/journal protocols are normalized before typed evidence is trusted.

### `detection`

**Purpose:** conservative service-fault detection, incident lifecycle, live detection bridge, controlled evaluation/characterization.

**Detector semantics:**

```text
load != loaded                  -> UNASSESSED
loaded + failed                 -> FAILED
loaded + inactive               -> INACTIVE
loaded + active+running+livePID -> HEALTHY
otherwise                       -> UNASSESSED
```

No probability/confidence is produced.

**Incident semantics:** OPENED / UPDATED / RESOLVED, exact-assessment deduplication, reclassification, recurrence with new incident identity.

**Live bridge:** consumes real service observations on the shared EventBus and manages bounded pending lifecycle/deferred service queues when publication fails.

### `lab`

**Purpose:** real controlled systemd ground truth on Sentinel-owned fixtures.

**Owns:**
- deterministic fixture specification;
- safe staging/runtime verification;
- `sentinel-x-lab-*` target constraints;
- controlled `SERVICE_INACTIVE` and `SERVICE_FAILED` interventions;
- monotonic ground-truth windows;
- recovery verification;
- experiment dataset artifacts.

**Must not become:** a general chaos-engineering platform.

### `dependency`

**Purpose:** frozen Phase-5 research implementation for dependency/propagation evidence and experiment provenance.

**Current consumer boundary:** no normal CLI/runtime import depends on this package; it is used by research/test/live-proof paths.

Submodules:

- `models.py` / `systemd.py` — typed Requires/Wants/After/Before manager evidence;
- `discovery.py` — bounded traversal over requirement relations;
- `graph.py` — versioned graph/topology representation;
- `propagation.py` — candidate and pairwise temporal evidence;
- `coverage.py` — bounded sampling/negative evidence semantics;
- `propagation_experiment.py` — deterministic controlled pair contracts and ground-truth evaluation;
- `propagation_live.py` — controlled live runner using real systemd manager state;
- `synthesis.py` — candidate-local conservative synthesis;
- `paired_synthesis.py` — controlled Requires/Wants paired contrast;
- `protocol.py` — pre-execution protocol provenance;
- `protocol_execution.py` — backend/boot-bound live execution.

This package is intentionally retained intact during Phase 5F so FULL evidence can be ablated against MINIMAL evidence. Its current public/identity complexity is not endorsed as the future target architecture.

### `_phase5f`

**Purpose:** private evaluation-only support for the Phase-5F falsification benchmark.

**Current scope (through the CI-proven 5F.6 ablation-scoring checkpoint):**
- `visible.py` owns one immutable gold-free `CaseSource` and the RAW/MINIMAL/FULL projection function;
- `gold.py` owns hidden scorer-only `CaseGold` and is deliberately not re-exported by the package root;
- `_common.py` contains shared opaque case/reference validation primitives;
- `baselines.py` owns the function-only B0, B1, and B1S deterministic comparators;
- `corpus.py`, `corpus_empirical.py`, and `corpus_transformations.py` own the frozen corpus contract, empirical binding, lineage/hidden-visible separation, export, and five deterministic adversarial transformations;
- `reasoner_output.py` owns the strict preregistered bare-JSON final-output parser/validator and performs no scoring or semantic repair;
- `scoring.py` owns the frozen hidden-gold M1–M8 primary-metrics scorer and exact RAW/MINIMAL/FULL scored-matrix validation;
- `deterministic_scoring.py` adapts B0/B1/B1S outputs to the frozen scorer while keeping baseline identity outside scorer rows;
- `execution.py` owns provider-neutral pre-score request/preflight contracts, deterministic B2/B3/B4 planning, retry policy, and no-truncation capacity checks without choosing a provider;
- `ollama_execution.py` pins the current local Ollama/Gemma execution identity and exposes only non-score-bearing identity/render/tokenizer/capacity-preflight behavior; normal score-bearing generation transport is still absent;
- `ablation.py` owns the frozen deletion-only seven mandatory field mappings and base-condition identity;
- `ablation_scoring.py` is the private scorer-side adapter for already-captured mandatory-ablation outputs. At checkpoint `07330c4d68d149075ce0c906e4f50e046d9221b1` it pins execution semantics SHA-256 `d0d36ca82238e70623a400a80ffb5e2c010c8db66e38edef7625a0a34fd1767d`, requires exactly 7×36×3 = 756 outputs, routes six mappings through `minimal` and one through `full`, keeps mapping identity outside the frozen scorer row, and aggregates M8 independently per mapping.

**Key invariants:**
- all three evidence conditions originate from one `CaseSource`;
- output shape does not reveal the condition name;
- evidence references are opaque `REF-####` values rather than semantic/outcome labels;
- MINIMAL accepts only preregistered factual categories;
- recursive hidden-gold fields are rejected from visible payloads;
- FULL consumes serialized frozen evidence without modifying `dependency/`;
- no new content-addressed identity family, Protocol hierarchy, result class, or public Sentinel-X API is introduced;
- B0 intentionally ignores topology, timing, boot provenance, and coverage so it remains a genuinely small comparator;
- B1 uses only MINIMAL factual scope/topology/timeline/coverage/boot facts and supports requirement-edge reachability without consuming current synthesis;
- B1S consumes only FULL current-synthesis serialization and does not upgrade forward temporal consistency alone into an observed effect;
- corpus-v1 keeps hidden gold/provenance physically separate from visible RAW/MINIMAL/FULL JSONL exports;
- derived adversarial variants inherit their empirical parent `source_run_group` and therefore are not independent empirical replications;
- hidden `CaseGold` may be consumed only by scorer-side evaluation after reasoner-visible output has been captured; it never enters prompt/request construction;
- the frozen 5F.5 scorer is not reopened to encode baseline or ablation identity; those identities remain in outer adapter/report metadata;
- mandatory ablation scoring accepts only the frozen seven mappings, exact 36-case corpus membership, and repeats 1/2/3; M8 is grouped independently per mapping;
- `scored_execution_authorized=False` remains the controlling pre-score state until the external score-bearing ablation execution boundary is frozen separately;
- `research/phase5f/capture_corpus_v1.py` is an operator-only evaluation harness: it reuses the frozen 5E.4 mutation runner and adds only bounded read-only `systemctl show` / `journalctl` sidecar capture.

`CaseGold` is a hidden benchmark/scoring artifact, not operational diagnosis truth and not a reasoner-visible object.

## Dependency Direction

Current high-level direction:

```text
config/core
   ^   ^
   |   |
observability / storage / systemd
                  |
                  v
               detection

lab uses systemd evidence for controlled experiments.
dependency consumes systemd/detection/lab evidence in research paths.
`_phase5f` consumes evaluation case material and serialized frozen evidence; operational/core/systemd modules do not depend on `_phase5f`.
```

Do not introduce a dependency from core/systemd operational primitives back into Phase-5 research synthesis.

## Data Flow

### Operational runtime

1. CLI loads typed config.
2. collectors read Linux/systemd/journal sources.
3. observations become `SentinelEvent` records.
4. EventBus publishes to subscribers/bridges.
5. correlation/detection produce derived events.
6. JSONL recorder persists configured events.

### Fault Lab

1. deterministic lab fixture is generated/staged/verified;
2. operator installs canonical unit under volatile systemd runtime path;
3. baseline is verified healthy;
4. fault is injected;
5. ground-truth window and observations are captured;
6. recovery is attempted and verified;
7. artifacts/dataset may be persisted.

### Phase-5 research path

1. read actual systemd manager dependency evidence;
2. build bounded graph/topology context;
3. bind service assessments/incidents to source events;
4. evaluate temporal direction and bounded coverage;
5. optionally bind controlled intervention evidence;
6. synthesize candidate-local/paired evidence;
7. capture exact protocol and backend/boot-bound execution provenance.

### Phase-5F falsification/evaluation path

1. assemble one gold-free `CaseSource` with opaque stable evidence references and project the same underlying case into RAW, MINIMAL, or FULL;
2. freeze visible corpus artifacts separately from hidden `CaseGold`, preserving source-run lineage and derived-case pseudoreplication grouping;
3. run deterministic baselines or, only after the relevant execution freeze, capture fresh reasoner outputs through a provider-specific execution boundary without exposing hidden gold;
4. validate reasoner final output through the strict 5F.4 bare-JSON boundary without semantic repair;
5. score captured outputs through the frozen 5F.5 scorer; hidden gold is introduced only at this scorer-side stage;
6. adapt deterministic B0/B1/B1S identity outside scorer rows through `deterministic_scoring.py`;
7. build mandatory visible-only deletion ablations through `ablation.py`, preserving the frozen seven mapping identities and base conditions;
8. score already-captured mandatory-ablation outputs through `ablation_scoring.py`, validating the exact 756-output matrix and keeping M8 independent per mapping;
9. keep normal score-bearing external ablation execution blocked until its exact request/order/seed/retry/output-capture identity is frozen separately.

## State Ownership

- EventBus owns subscriber registry only; subscribers own their derived state.
- journal checkpoint store owns cursor/checkpoint durability.
- incident tracker owns open incident lifecycle state in memory/snapshots; hard-crash durability is not claimed.
- detection bridge owns bounded pending/deferred in-memory queues.
- Fault Lab models own experiment/ground-truth records; systemd remains the actual executor.
- Phase-5 evidence records are immutable evidence/provenance models, not operational runtime state owners.

## Persistence Architecture

Current persistence includes:

- runtime JSONL event files;
- private journald checkpoint state;
- controlled Fault Lab dataset bundles;
- detection benchmark/characterization artifacts;
- external archived Phase-5 live-proof evidence (operator-managed, outside source repository).

Not all runtime state is crash durable. See `KNOWN_EXCEPTIONS.md` and `TECH_DEBT.md`.

## Transaction / Delivery Boundaries

- transactional collector emissions commit only after successful EventBus publication;
- service-state observations are intentionally nontransactional;
- journald checkpoint progression is tied to acknowledged evidence delivery semantics;
- failed derived publication may retain exact pending evidence for retry;
- no fake exactly-once guarantee is claimed.

## Concurrency Model

- EventBus uses thread-safe subscriber management and publication snapshots;
- runtime collector execution/failure is isolated;
- Phase-5 live experiment sampling is concurrent with controlled intervention;
- concurrency is deliberately limited rather than a generic async architecture.

## External Integrations

Current native external interfaces:

- Linux `/proc`/filesystem sources;
- `systemctl`;
- journald evidence;
- systemd runtime unit directory for controlled lab fixtures.

There is currently no OpenTelemetry, Prometheus ingestion, or external chaos backend. A private Phase-5F local Ollama/Gemma evaluation adapter exists for pinned identity, render/tokenizer checks, and capacity/live preflight, but it is not a production LLM integration and normal score-bearing generation transport remains absent.

## Authentication / Authorization / Privilege

No application authentication/authorization layer exists because this is not a multi-user service.

Controlled lab proofs may use operator-authorized sudo. Production privilege separation is not implemented and is an explicit future hardening gate.

## Error Propagation

Principles:

- malformed/unbounded protocol input fails explicitly;
- one EventBus subscriber failure does not prevent later subscribers;
- collector failures are isolated;
- capacity exhaustion is explicit;
- source/provenance/identity drift is rejected at relevant evidence boundaries;
- live-proof cleanup/recovery is part of the operational contract.

## Configuration

TOML configuration under `config/` controls runtime/collector/systemd/journal/storage behavior. Phase-5 research experiments use typed policy/protocol objects rather than normal runtime wiring.

## Deployment

Current validated usage is local Linux/Fedora development/research execution via Python virtual environment and systemd lab fixtures. No production deployment architecture is claimed.

## Major Architectural Risks

1. Phase-5 representational complexity can become self-referential correctness work.
2. Phase-5 research subsystem has no normal CLI consumer.
3. Cross-environment generalization is untested.
4. Some evidence/retry state is memory-only.
5. Lab privilege model is research-grade, not production-grade.
6. External reasoning value is unproven until Phase 5F.

## Related ADRs

See [`adr/README.md`](adr/README.md).
