# System Constraints and Invariants

These properties must remain true regardless of local implementation convenience unless an ADR explicitly changes them with new evidence.

## Architectural Invariants

### INV-001 — Native execution vs intelligence separation
systemd/native Linux mechanisms perform lifecycle execution; Sentinel-X reasons about evidence and policy above them.

### INV-002 — One shared EventBus/runtime path
Do not introduce a competing event bus/runtime merely for a new feature.

### INV-003 — Evidence before interpretation
Raw/native protocol data is bounded, decoded, validated, and typed before interpretive claims are assigned.

### INV-004 — Phase-5 FULL remains frozen through 5F
Do not simplify/remove Phase-5 evidence/protocol/synthesis structures before Full-vs-Minimal ablation. The current architecture is the experimental FULL control.

## Evidence / Data Integrity Invariants

### INV-010 — Unknown remains explicit
Ambiguous, transitional, unsafe, or insufficient states remain `UNASSESSED`/insufficient rather than guessed.

### INV-011 — Missing is not zero
Missing transition/sample/context data is not represented as zero, healthy, or no effect.

### INV-012 — Duplicate evidence can be preferable to silent loss
Where delivery is at-least-once, do not create fake exactly-once behavior by dropping evidence silently.

### INV-013 — Bounded structures fail explicitly
Important bounded collections/queues raise/record capacity failure rather than silently evict unless eviction is explicitly designed.

### INV-014 — Boot/scope incompatibility is not silently mergeable
Evidence from incompatible boot/context scopes cannot be combined as if comparable.

### INV-015 — Persisted evidence identity remains auditable
Archived/persisted artifacts retain enough identity/provenance to detect material drift or mismatched content.

## Epistemic / Research Invariants

### INV-020 — Topology is not effect
A Requires/Wants/After/Before edge is structural evidence, not proof that a runtime effect occurred.

### INV-021 — Temporal order is not causality
Forward timing may support a directional hypothesis; it does not establish cause.

### INV-022 — Negative claims require adequate coverage
“No effect observed” requires explicitly sufficient bounded observation. Sparse/no samples yield insufficient evidence.

### INV-023 — Counterevidence cannot be overwritten
Supportive evidence must not hide reverse sequence or other counterevidence.

### INV-024 — Intervention remains scoped
A controlled intervention strengthens evidence for the exact experiment; one run does not establish a universal causal law.

### INV-025 — No invented probability/confidence
No value is presented as validated probability/confidence without a real calibrated method.

### INV-026 — Claim strength never exceeds evidence
Any current/future reasoner output stays within the maximum claim strength justified by evidence and scope.

## Detection Invariants

### INV-030 — Conservative service detector

- non-loaded -> UNASSESSED
- loaded+failed -> FAILED
- loaded+inactive -> INACTIVE
- loaded+active+running+live PID -> HEALTHY
- otherwise -> UNASSESSED

### INV-031 — UNASSESSED does not resolve an open incident
Insufficient/ambiguous state cannot be treated as recovery.

### INV-032 — Exact assessment retry is idempotent
The same assessment identity does not mutate incident lifecycle twice.

## Journal / Delivery Invariants

### INV-040 — Do not blindly reset stale journal cursor
Recovery preserves explicit evidence continuity semantics.

### INV-041 — Checkpoint progression follows acknowledged publication semantics
Journal continuity does not claim delivered evidence that was not accepted according to the contract.

## Fault Lab Safety Invariants

### INV-050 — Mutation is restricted to canonical Sentinel lab targets
Only explicit `sentinel-x-lab-*` fixtures under approved runtime systemd paths are eligible.

### INV-051 — Recovery verification is part of experiment success
Command exit alone is not success. Post-fault healthy/recovery verification is required.

### INV-052 — Ground truth does not come from detector output
Fault ground-truth windows are anchored to intervention, not inferred from the detector.

## Phase-5F Evaluation Invariants

### INV-060 — Preregistration precedes scored result inspection
Primary metrics, corpus rules, thresholds, and kill criteria are frozen before scored benchmark results.

### INV-061 — Same cases across evidence conditions
RAW/MINIMAL/FULL comparisons use the same underlying case and hidden label.

### INV-062 — No hidden-label leakage
Visible evidence/projection code does not receive or encode hidden expected outcomes.

### INV-063 — Fresh/blinded external reasoner context
LLM evaluation excludes repository implementation, hidden labels, expected outcomes, answer-revealing prior conversation, and previous condition outputs.

### INV-064 — Full complexity must earn survival
FULL survives only if it provides repeatable independent benefit over MINIMAL evidence.

## Conditions Under Which an Invariant May Change

An invariant may change only when:

1. new empirical evidence invalidates its assumption;
2. a security/correctness defect makes it unsafe;
3. a later phase has explicit requirements conflicting with it;
4. an ADR documents decision, alternatives, consequences, migration, and reversal;
5. relevant tests/documentation are updated in the same reviewed change.
