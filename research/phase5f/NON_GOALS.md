# Phase 5F — Non-Goals

Status: PRE-REGISTERED DRAFT

Phase 5F is not a product/feature expansion phase.

## Hard Non-Goals Until Phase-5F Verdict

Do not build or expand:

- host observability features;
- fault types;
- dependency relation support;
- new synthesis models;
- new identity families;
- new protocol layers;
- autonomous remediation;
- generic RCA ranking;
- numeric probability/confidence;
- distributed tracing collector;
- new storage backend;
- plugin ecosystem;
- UI/dashboard;
- autonomous/agent runtime;
- broad chaos engine;
- stable public evaluation framework API.

## Do Not Refactor FULL Before Ablation

Do not:

- rewrite `protocol.py`;
- delete `paired_synthesis.py`;
- mass-rename current Phase-5 IDs;
- collapse current dependency exports;
- centralize validators by modifying frozen research models;
- change current FULL evidence semantics.

FULL is the reference condition.

## Evaluation Non-Goals

Phase 5F does not attempt to:

- benchmark every AIOps/RCA platform;
- prove production incident accuracy;
- estimate operator MTTR in production;
- validate cross-distro/systemd generalization;
- prove causal effects statistically;
- measure final production overhead/SLO;
- evaluate hidden chain-of-thought;
- select a permanent LLM vendor/model;
- create a marketing leaderboard.

## Research Non-Goal

The next empirical work is **not** to discover more obvious Requires/Wants semantics. Existing pair experiments are reference cases/harness validation only.
