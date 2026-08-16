# Phase 5F — Baselines

Status: FROZEN — preregistered at `f47b5cb764030ea8c78d53ff76ebe0859bb4ca29` before scored results

The benchmark must include fair simple alternatives. Sentinel-X does not get weak strawmen.

## B0 — State Rule

Minimum deterministic service-state baseline.

Conceptual policy:

```text
dependent INACTIVE/FAILED observed -> EFFECT_OBSERVED
only healthy observations          -> simple no-effect observation
no usable state                     -> INSUFFICIENT
```

For common scoring, output is mapped to the five-class schema. B0 deliberately does not use full topology/provenance/coverage semantics.

## B1 — Simple Graph + Time Heuristic

Strong small deterministic baseline without current Phase-5 synthesis hierarchy.

Algorithm:

1. if no compatible candidate/scope can be established -> `INSUFFICIENT`;
2. if multiple equally plausible candidates remain -> `AMBIGUOUS`;
3. if downstream anomaly clearly precedes source transition -> `COUNTEREVIDENCE`;
4. if structurally reachable and downstream anomaly follows source within the preregistered window -> `EFFECT_OBSERVED`;
5. if no anomaly, usable downstream samples are healthy, and bounded coverage is sufficient -> `BOUNDED_NEGATIVE`;
6. otherwise -> `INSUFFICIENT`.

B1 must remain small enough that its complexity is itself a meaningful comparator.

## B1S — Current Sentinel-X Deterministic Synthesis Projection

Not a new feature. Existing frozen Phase-5 synthesis is mapped to common evaluation labels.

Question:

> Does current Phase-5 deterministic machinery outperform much smaller B1 on HARD cases?

If not, synthesis becomes a Phase-5R collapse candidate.

## B2 — Strong LLM with RAW Evidence

- fresh isolated context;
- RAW bounded evidence only;
- no repository/project history;
- no hidden labels/expected outcome;
- no previous condition output;
- same model/task/output schema/configuration as B3/B4.

## B3 — Same LLM with MINIMAL Evidence

Primary thesis condition.

Question:

> Does MINIMAL evidence materially improve reasoning compared with RAW?

## B4 — Same LLM with FULL Evidence

Full frozen applicable Phase-5 representation.

Question:

> Does FULL provide repeatable independent value beyond MINIMAL?

If not, FULL complexity is not justified by sunk cost.

## B5 — Optional Human Operator Subset

Optional small subset only after internal protocol works; not required for primary 5F verdict.

## Fairness Requirements

For B2/B3/B4:

- identical model identifier/version;
- identical task wording;
- identical output schema;
- identical decoding settings;
- fresh context;
- condition name not disclosed;
- evidence block is the intended difference;
- context/token capacity must be adequate for all views or the comparison is redesigned before scoring.
