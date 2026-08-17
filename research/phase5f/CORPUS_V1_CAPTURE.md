# Phase 5F.3 Corpus-v1 Capture Protocol

Status: CORRECTIVE RE-FREEZE REQUIRED — second live attempt exposed reverse-transform assumption beyond empirical contract

This document is an execution/capture protocol derived from the six Phase-5F preregistration documents. It does **not** change `THESIS.md`, `NON_GOALS.md`, `EVALUATION_PROTOCOL.md`, `BASELINES.md`, `METRICS.md`, or `KILL_CRITERIA.md`.

## 1. Purpose

Phase 5F.3 exists to create and freeze the blinded corpus used by later deterministic and LLM comparisons. The corpus must exist before any scored reasoner output is inspected.

5F.3 is split into two engineering checkpoints:

- **5F.3A — Corpus Contract & Capture Harness:** define/freeze corpus structure, case plan, lineage rules, transformations, bounded read-only sidecar instrumentation, observer pilot, and operator harness.
- **5F.3B — Live Corpus Capture & Freeze:** execute the frozen harness on Fedora, audit the resulting corpus, archive/hash it, and freeze it before 5F.4.

5F.3A produces no benchmark score and does not run an LLM/human reasoner.

## 2. Frozen Corpus-v1 Counts

Corpus-v1 contains exactly **36** opaque cases:

- 8 empirical Requires executions with an observed downstream anomaly/effect;
- 8 empirical Wants executions with bounded-negative downstream evidence;
- 4 insufficient-coverage derivatives;
- 4 reverse/counterevidence derivatives;
- 4 temporal-distractor derivatives;
- 4 cross-boot/provenance-invalid derivatives;
- 4 multiple-candidate ambiguity derivatives.

Exactly **16** cases are empirical and **20** are deterministic adversarial derivatives.

The HARD subset contains exactly **28** cases:

- empirical CASE-0009 through CASE-0016;
- adversarial CASE-0017 through CASE-0036.

Derived cases are not independent empirical replications.

## 3. Opaque Case IDs and Empirical Relation Assignment

Requires empirical cases:

```text
CASE-0001 ... CASE-0008
```

Wants empirical cases:

```text
CASE-0009 ... CASE-0016
```

Case IDs are opaque evaluation identifiers. The reasoner is not told that a numeric range maps to Requires or Wants.

## 4. Frozen Empirical Execution Order

The 16 corpus executions use this exact acquisition order:

```text
CASE-0001
CASE-0009
CASE-0010
CASE-0002
CASE-0003
CASE-0011
CASE-0012
CASE-0004
CASE-0005
CASE-0013
CASE-0014
CASE-0006
CASE-0007
CASE-0015
CASE-0016
CASE-0008
```

The ABBA-like ordering reduces simple fixed arm-order drift. It is only an acquisition-order control; it does **not** establish statistical counterbalancing, independence, replication, or a population claim.

## 5. Single-Boot Requirement

The observer pilot and all 16 empirical corpus executions must use one normalized Linux boot ID.

If the boot changes at any point:

1. abort the campaign;
2. do not retain a partial corpus as corpus-v1;
3. clean the controlled fixture pair;
4. restart corpus-v1 from the beginning on one boot.

A cross-boot adversarial case is created later as a deterministic derivative; the empirical campaign itself is single-boot.

## 6. Frozen Mutation Path

The harness does not introduce a competing fault/mutation engine.

All controlled mutation/evidence execution uses the frozen `ControlledPropagationProtocolBoundRunner` and frozen Phase-5E.4 semantics.

The evaluation harness may install/remove only its dedicated `p5f3-corpus-*` lab unit artifacts as an operator setup/cleanup action. It does not modify production services.

## 7. RAW Read-only Sidecar

The RAW sidecar is evaluation-only instrumentation outside the production package.

It may perform only bounded read operations using:

- `/usr/bin/systemctl show`;
- `/usr/bin/journalctl`.

It does not mutate service state.

### 7.1 systemctl sampling bounds

```text
interval_seconds = 0.10
max_rounds = 160
max_stdout_or_stderr_per_read = 32768 bytes
```

Properties:

```text
Id
LoadState
ActiveState
SubState
MainPID
ExecMainCode
ExecMainStatus
StateChangeTimestampMonotonic
```

The sidecar must obtain at least one completed source/dependent sample round **before** invoking the frozen live runner. This prevents the RAW stream from beginning only after the controlled fault has already started.

After the frozen live runner returns successfully, the sidecar must remain active until at least one completed source/dependent sample round has `captured_monotonic_usec >= ground_truth.ended_monotonic_usec`. The wait is explicitly bounded at 2.0 seconds. If that post-fault proof sample is not captured within the bound, the run fails and is not a corpus case. This rule was added after the first 5F.3B attempt exposed a race in which immediate sidecar shutdown could leave the last RAW sample just before the recorded fault end.

The second 5F.3B attempt exposed a separate adversarial-derivation contract bug after the empirical campaign progressed: reverse/counterevidence derivation assumed every empirical effect parent had a target `incident_timeline`. That is not guaranteed because controlled-coverage anomaly evidence is sufficient for `EFFECT_OBSERVED` even when `pairwise_evidence` is absent. The corrected reverse transform must create only the **derived adversarial** target timeline when missing; it must not fabricate or backfill pairwise timing into the empirical parent.

A nonzero `systemctl show` return code is a sidecar error and fails the run.

### 7.2 journal bounds

```text
max_lines = 256
max_stdout = 524288 bytes
```

Journal capture is limited to the controlled source/dependent units and the bounded run time window.

A nonzero journal command return code fails the run.

### 7.3 RAW capture validity

An empirical RAW capture is valid only if:

- sidecar schema matches corpus-v1;
- source/dependent units match the bound execution;
- boot ID matches the bound execution;
- capture starts no later than fault start;
- capture ends no earlier than fault end;
- at least four systemctl sample rows exist;
- actual sample timestamps begin before/on fault start and extend through fault end;
- sidecar error count is zero;
- journal evidence is present as a bounded mapping.

## 8. Observer Pilot

Before corpus capture, run four non-corpus pilot executions:

1. Requires without sidecar;
2. Requires with sidecar;
3. Wants without sidecar;
4. Wants with sidecar.

The campaign may proceed only if:

- Requires remains `AFFECTED_ANOMALY_OBSERVED` with and without sidecar;
- Wants remains `BOUNDED_NEGATIVE_EVIDENCE` with and without sidecar;
- frozen-run sampling coverage remains bounded in all pilot executions;
- post-recovery verification succeeds;
- sidecar reports no command/capacity error;
- all pilot observations share one boot.

The stored flag:

```text
material_observer_effect_detected = false
```

means only that this material qualitative gate passed. It is **not** a statistical proof that instrumentation has zero observer effect.

## 9. Empirical Case Binding

Each empirical case binds:

- one actual `ControlledPropagationProtocolBoundExecution`;
- one bounded RAW sidecar capture;
- one environment snapshot;
- one hidden gold record;
- one exact evidence-reference lineage map.

Requires cases are accepted only if the frozen controlled record is `AFFECTED_ANOMALY_OBSERVED`.

Wants cases are accepted only if the frozen controlled record is `BOUNDED_NEGATIVE_EVIDENCE`.

An unexpected qualitative outcome fails the campaign instead of silently relabeling the case.

## 10. Hidden Source-run Grouping

Each empirical case receives one unique hidden source-run group:

```text
CASE-0001 -> RUN-0001
...
CASE-0016 -> RUN-0016
```

Every adversarial derivative inherits its empirical parent's exact source-run group.

Later analysis must not treat an empirical case and its derivatives as independent empirical observations.

## 11. Visible Views and Lineage

Every case owns one gold-free `CaseSource`. RAW, MINIMAL, and FULL are projected from that same source.

For every case:

- `case_id`, task, environment, and visible schema must be equal across all three views;
- visible evidence references are opaque `REF-####` identifiers;
- a hidden `lineage_by_ref` map must cover exactly the union of visible refs across RAW/MINIMAL/FULL;
- hidden gold refs must resolve to visible refs;
- condition labels are not serialized inside the evidence bundle.

5F.3A defines these invariants. 5F.3B proves them on the actual captured corpus-v1 archive.

## 12. Empirical Visible Evidence

Empirical cases include factual scope/topology/timeline/coverage/provenance/intervention evidence plus bounded RAW sidecar evidence.

FULL additionally carries applicable frozen Phase-5 records, including candidate, controlled coverage, pairwise evidence when present, controlled experiment record, live run, protocol-bound execution, and candidate-local synthesis.

FULL is consumed as frozen evidence; Phase-5E.4 models are not modified.

## 13. Frozen Adversarial Case Plan

### 13.1 Insufficient coverage

```text
CASE-0017 <- CASE-0009
CASE-0018 <- CASE-0010
CASE-0019 <- CASE-0011
CASE-0020 <- CASE-0012
```

Transformation:

- force `largest_gap_usec > max_sample_gap_usec`;
- remove target RAW samples from the middle third of the controlled window to create a matching deterministic evidence gap;
- remove stale FULL derived controlled/synthesis records that no longer apply;
- hidden gold = `INSUFFICIENT`, `must_abstain=true`.

### 13.2 Reverse/counterevidence

```text
CASE-0021 <- CASE-0001
CASE-0022 <- CASE-0002
CASE-0023 <- CASE-0003
CASE-0024 <- CASE-0004
```

Transformation:

- move the target anomaly to 100000 usec before the source transition;
- remove the parent journal excerpt that would preserve stale forward chronology;
- rewrite/add the RAW target anomaly at the reverse timestamp;
- if the empirical parent has no target `incident_timeline` because `pairwise_evidence` was absent, synthesize the adversarial target timeline from the observed target anomaly at the derived reverse timestamp and mark that evidence lineage as derived rather than attributing exact pairwise timing to the empirical parent;
- remove stale FULL derived controlled/synthesis records;
- hidden gold = `COUNTEREVIDENCE`.

### 13.3 Temporal distractor

```text
CASE-0025 <- CASE-0005
CASE-0026 <- CASE-0006
CASE-0027 <- CASE-0007
CASE-0028 <- CASE-0008
```

Transformation:

- add a same-boot unrelated failed unit close in time to the true source;
- do not add a dependency path from that unit to the target;
- preserve valid parent FULL evidence because parent evidence itself is unchanged;
- mark the distractor ref invalid in hidden gold;
- hidden classification remains `EFFECT_OBSERVED`.

### 13.4 Cross-boot invalid provenance

```text
CASE-0029 <- CASE-0013
CASE-0030 <- CASE-0014
CASE-0031 <- CASE-0015
CASE-0032 <- CASE-0016
```

Transformation:

- add failed target observation/timeline evidence using a deterministic different boot ID;
- preserve valid parent FULL evidence;
- mark wrong-boot refs invalid;
- hidden classification remains `BOUNDED_NEGATIVE`.

This family intentionally tests whether a naïve state-only rule is fooled while a boot-aware reasoner rejects incompatible evidence.

### 13.5 Multiple-candidate ambiguity

```text
CASE-0033 <- CASE-0001
CASE-0034 <- CASE-0003
CASE-0035 <- CASE-0005
CASE-0036 <- CASE-0007
```

Transformation:

- remove the explicit source identity from case scope;
- preserve the original requirement path;
- add a second plausible Wants source and a temporally compatible source transition;
- remove stale FULL derived controlled/synthesis records;
- hidden gold = `AMBIGUOUS`, `must_abstain=true`.

## 14. Stale FULL Evidence Policy

A transformation must not preserve answer-like parent FULL records when its mutation invalidates the preconditions or chronology represented by those records.

The following FULL-only kinds are stripped by insufficient/reverse/multiple-candidate transforms:

```text
controlled_coverage
pairwise_evidence
controlled_record
live_run
protocol_execution
synthesis
```

This is not evidence suppression. Those records describe the unmodified parent case and would be false/stale evidence after the transformation.

Temporal-distractor and cross-boot transforms preserve valid parent FULL records because they add distractor evidence without changing the original parent event/evidence chain.

## 15. Corpus Audit Gate

Before export, corpus-v1 must satisfy all of the following:

- exactly 36 case IDs `CASE-0001..CASE-0036`;
- exact family counts 8/8/4/4/4/4/4;
- exactly 16 empirical + 20 adversarial;
- exactly 28 HARD cases;
- 16 unique empirical source-run groups;
- each adversarial source-run group equals its empirical parent's group;
- all adversarial parents exist and are empirical;
- RAW/MINIMAL/FULL outer lineage fields agree per case;
- hidden lineage covers every visible ref exactly;
- no scored reasoner output is present.

Any failure invalidates the candidate corpus archive.

## 16. Frozen Export Layout

The exporter creates a new directory atomically and refuses an existing destination.

```text
corpus-v1/
├── visible/
│   ├── raw.jsonl
│   ├── minimal.jsonl
│   └── full.jsonl
├── hidden/
│   ├── gold.jsonl
│   └── provenance.jsonl
├── observer_pilot.json
├── manifest.json
└── SHA256SUMS
```

`hidden/` and its files are not reasoner inputs. Hidden gold/provenance files are written with restrictive permissions.

The final directory is additionally archived as ZIP and the ZIP SHA-256 is printed by the harness.

## 17. Environment Metadata

The visible environment snapshot records:

- boot ID;
- opaque host alias;
- kernel release;
- systemd manager version;
- OS `PRETTY_NAME`;
- systemctl path;
- journalctl path;
- RAW sidecar schema.

This is selected environment provenance, not a claim that the complete host environment has been captured.

## 18. Failure / Restart Rules

Stop and do not freeze corpus-v1 if any of these occur:

- boot changes during pilot/campaign;
- observer pilot fails;
- Requires/Wants empirical class differs from the frozen expected class;
- frozen-run sampling becomes unbounded;
- recovery is not verified;
- sidecar command/thread/capacity error occurs;
- RAW capture does not span the controlled interval;
- corpus audit fails;
- output destination already exists;
- cleanup cannot establish a safe final lab state.

Partial output is not a valid corpus-v1.

## 19. No-scoring Boundary

5F.3A/5F.3B must not:

- run B2/B3/B4;
- inspect scored LLM output;
- tune corpus rules after seeing reasoner performance;
- change frozen Phase-5F metrics or kill criteria;
- label derived cases as empirical replications;
- claim external/general population validity.

After 5F.3B freezes corpus-v1, the project may enter 5F.4 structured reasoner-output execution under the already frozen evaluation protocol.
