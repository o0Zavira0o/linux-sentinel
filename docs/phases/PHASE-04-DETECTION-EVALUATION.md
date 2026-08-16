# Phase 04 — Detection / Evaluation / Incident Lifecycle / Live Integration

## Status
FROZEN / CLOSED

## Objective
Build a conservative service-state detector and evaluate it against real controlled ground truth without inventing confidence or hiding missingness.

## 4A — Conservative State Detection

```text
load state not loaded           -> UNASSESSED
failed                          -> FAILED
inactive                        -> INACTIVE
loaded+active+running+live PID  -> HEALTHY
transitional/ambiguous/PIDless  -> UNASSESSED
```

No confidence score, causal claim, or ML probability.

Historical commit: `d271f2dce9a216e950ee794345d0c51f0760803f`.

## 4B — Ground-Truth Evaluation / Repeated Benchmark

Detector output is compared with Fault Lab ground truth; ground truth does not leak into detector behavior.

Preserved:
- signed offsets;
- missing measurements;
- false positives;
- `UNASSESSED`;
- per-mode and overall evidence.

Historical commits:
- `fb20982088345e750c441d7bd4bfa50a74af8386`
- `4fd18c3cc589c6d8acc624bf9f64e6d46ea4b4a7`

## 4C — Incident Lifecycle

OPENED / UPDATED / RESOLVED, exact-assessment deduplication, recovery, reclassification, recurrence, drift/monotonic guards.

Hard-crash durable incident continuity is not claimed.

Historical commit: `19da825d3515f663c24d7c6fd987a53629478183`.

## 4D — Live EventBus Detection

Real service observations -> shared EventBus -> detector -> incident tracker -> lifecycle events -> shared EventBus/storage.

Backpressure uses a bounded pending lifecycle outbox plus deferred service FIFO so failed lifecycle publication does not silently discard later nontransactional service evidence.

Historical commit: `421e8fbbf1bc309ff7c08dca3e49c0044eb40eac`.

## 4E — Empirical Detection Characterization

Historical module name: calibration.

Final Phase-4 commit: `f873c7d64bf0aa52bdf6b58390acadb49224a77c`.

Controlled live benchmark:

- 10 runs;
- 5 `service_inactive`;
- 5 `service_failed`;
- detection-covered 10/10;
- classification-complete 10/10;
- healthy-control-clean 10/10;
- observed healthy false positives 0;
- observed missed expected fault-state samples 0;
- observed wrong-anomaly samples 0.

These are controlled-lab observations, not production accuracy estimates.

Inactive exact transition timing was unavailable in tested inactive cases and remained explicitly missing.

## Strategic Interpretation
Current detector is an important deterministic baseline, not a claimed state-of-the-art anomaly detector.
