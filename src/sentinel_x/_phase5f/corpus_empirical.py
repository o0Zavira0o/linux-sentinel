"""Private Phase-5F empirical corpus binding from frozen live evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from sentinel_x.dependency.models import (
    DependencyRelation,
    source_property_for_relation,
)
from sentinel_x.dependency.propagation_experiment import (
    ControlledPropagationEvidenceClass,
)
from sentinel_x.dependency.protocol_execution import (
    ControlledPropagationProtocolBoundExecution,
)
from sentinel_x.dependency.synthesis import synthesize_propagation_evidence

from ._common import validate_case_id
from .corpus import (
    EMPIRICAL_BOUNDED_NEGATIVE_FAMILY,
    EMPIRICAL_EFFECT_FAMILY,
    PHASE5F_SIDECAR_SCHEMA_VERSION,
    REQUIRES_EMPIRICAL_CASE_IDS,
    WANTS_EMPIRICAL_CASE_IDS,
    CorpusCase,
    _json_copy,
    _required_nonnegative_int,
    empirical_relation_for_case,
)
from .gold import CaseGold
from .visible import CaseSource


def build_empirical_corpus_case(
    *,
    case_id: str,
    execution: ControlledPropagationProtocolBoundExecution,
    raw_capture: Mapping[str, object],
    environment: Mapping[str, object],
) -> CorpusCase:
    """Build one empirical corpus case from a frozen bound execution and RAW sidecar."""

    validate_case_id(case_id)
    if case_id not in (*REQUIRES_EMPIRICAL_CASE_IDS, *WANTS_EMPIRICAL_CASE_IDS):
        raise ValueError("case_id is not assigned to empirical corpus-v1")
    if not isinstance(execution, ControlledPropagationProtocolBoundExecution):
        raise ValueError(
            "execution must be ControlledPropagationProtocolBoundExecution"
        )
    expected_relation = empirical_relation_for_case(case_id)
    actual_relation = execution.live_run.pair_artifact.spec.requirement_relation
    if actual_relation is not expected_relation:
        raise ValueError("execution relation does not match frozen empirical case plan")

    _validate_raw_capture(execution, raw_capture)
    environment_value = _json_copy(environment, field_name="environment")
    if not isinstance(environment_value, dict):
        raise AssertionError("environment JSON copy must remain a mapping")
    environment_copy = cast(dict[str, object], environment_value)
    if environment_copy.get("boot_id") != execution.attempt.boot_id:
        raise ValueError("environment boot_id must match bound execution boot")

    live_run = execution.live_run
    record = live_run.experiment_record
    coverage = record.controlled_coverage_evidence
    candidate = live_run.candidate
    ground_truth = record.injection_outcome.ground_truth
    duration_usec = ground_truth.duration_usec
    if duration_usec is None or duration_usec <= 0:
        raise ValueError(
            "empirical execution requires closed positive-duration ground truth"
        )

    evidence_class = record.evidence_class
    if expected_relation is DependencyRelation.REQUIRES:
        if (
            evidence_class
            is not ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED
        ):
            raise ValueError(
                "Requires empirical case did not observe the preregistered effect"
            )
        family = EMPIRICAL_EFFECT_FAMILY
        classification = "EFFECT_OBSERVED"
        summary_status = "inactive" if coverage.inactive_count else "failed"
    else:
        if (
            evidence_class
            is not ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE
        ):
            raise ValueError(
                "Wants empirical case did not produce bounded-negative evidence"
            )
        family = EMPIRICAL_BOUNDED_NEGATIVE_FAMILY
        classification = "BOUNDED_NEGATIVE"
        summary_status = "healthy"

    refs: dict[str, str] = {}
    items: list[dict[str, object]] = []

    def add(
        ref: str,
        kind: str,
        *,
        raw: object | None = None,
        minimal: object | None = None,
        full: object | None = None,
        lineage: str,
    ) -> None:
        item: dict[str, object] = {"ref": ref, "kind": kind}
        if raw is not None:
            item["raw"] = raw
        if minimal is not None:
            item["minimal"] = minimal
        if full is not None:
            item["full"] = full
        items.append(item)
        refs[ref] = lineage

    scope = {
        "source_unit": candidate.dependency_unit,
        "target_unit": candidate.dependent_unit,
        "boot_id": candidate.boot_id,
        "analysis_window_usec": duration_usec,
    }
    add(
        "REF-0001",
        "scope",
        raw={
            "source_unit": candidate.dependency_unit,
            "target_unit": candidate.dependent_unit,
            "boot_id": candidate.boot_id,
            "capture_started_monotonic_usec": raw_capture["started_monotonic_usec"],
            "capture_ended_monotonic_usec": raw_capture["ended_monotonic_usec"],
        },
        minimal=scope,
        full=scope,
        lineage=f"execution:{execution.execution_id}:scope",
    )
    topology = {
        "subject_unit": candidate.dependent_unit,
        "object_unit": candidate.dependency_unit,
        "relation": candidate.requirement_relation.value,
        "boot_id": candidate.boot_id,
    }
    add(
        "REF-0002",
        "topology_relation",
        raw={
            "manager_property": source_property_for_relation(
                candidate.requirement_relation
            ),
            "subject_unit": candidate.dependent_unit,
            "object_unit": candidate.dependency_unit,
            "boot_id": candidate.boot_id,
        },
        minimal=topology,
        full=topology,
        lineage=f"candidate:{candidate.candidate_id}:requirement",
    )
    source_timeline = {
        "unit": candidate.dependency_unit,
        "state": "inactive",
        "transition_monotonic_usec": ground_truth.started_monotonic_usec,
        "boot_id": candidate.boot_id,
    }
    add(
        "REF-0003",
        "incident_timeline",
        minimal=source_timeline,
        full=source_timeline,
        lineage=f"experiment:{record.record_id}:source-ground-truth",
    )

    target_ref: str | None = None
    if record.pairwise_evidence is not None:
        target_ref = "REF-0004"
        target_timeline = {
            "unit": candidate.dependent_unit,
            "state": summary_status,
            "transition_monotonic_usec": (
                record.pairwise_evidence.affected_incident.temporal_usec
            ),
            "boot_id": candidate.boot_id,
        }
        add(
            target_ref,
            "incident_timeline",
            minimal=target_timeline,
            full=target_timeline,
            lineage=f"pairwise:{record.pairwise_evidence.evidence_id}:affected",
        )

    observation = {
        "unit": candidate.dependent_unit,
        "status": summary_status,
        "boot_id": candidate.boot_id,
    }
    add(
        "REF-0005",
        "observation",
        minimal=observation,
        full=observation,
        lineage=f"coverage:{coverage.evidence_id}:status-summary",
    )
    coverage_summary = {
        "unit": candidate.dependent_unit,
        "boot_id": candidate.boot_id,
        "window_start_usec": coverage.window_start_usec,
        "window_end_usec": coverage.window_end_usec,
        "largest_gap_usec": coverage.largest_observed_gap_usec,
        "max_sample_gap_usec": coverage.max_sample_gap_usec,
        "healthy_count": coverage.healthy_count,
        "inactive_count": coverage.inactive_count,
        "failed_count": coverage.failed_count,
        "unassessed_count": coverage.unassessed_count,
    }
    add(
        "REF-0006",
        "coverage",
        minimal=coverage_summary,
        full=coverage_summary,
        lineage=f"coverage:{coverage.evidence_id}:summary",
    )
    provenance = {
        "boot_id": candidate.boot_id,
        "candidate_id": candidate.candidate_id,
        "graph_version_id": candidate.graph_version_id,
        "topology_id": candidate.topology_id,
        "requirement_observed_at": candidate.requirement_observed_at.isoformat(),
        "cross_boot_temporal_comparison_permitted": False,
    }
    add(
        "REF-0007",
        "provenance",
        minimal=provenance,
        full=provenance,
        lineage=f"candidate:{candidate.candidate_id}:provenance",
    )
    intervention = {
        "experiment_id": ground_truth.experiment_id,
        "scenario_id": ground_truth.scenario_id,
        "target_unit": ground_truth.target_unit,
        "fault_mode": ground_truth.fault_mode.value,
        "started_monotonic_usec": ground_truth.started_monotonic_usec,
        "ended_monotonic_usec": ground_truth.ended_monotonic_usec,
        "boot_id": candidate.boot_id,
        "controlled_lab_observation_only": True,
    }
    add(
        "REF-0008",
        "intervention_context",
        minimal=intervention,
        full=intervention,
        lineage=f"experiment:{record.record_id}:intervention",
    )

    add(
        "REF-0010",
        "systemctl_samples",
        raw={
            "samples": raw_capture["systemctl_samples"],
            "interval_seconds": raw_capture.get("interval_seconds"),
            "max_rounds": raw_capture.get("max_rounds"),
            "bounded": True,
        },
        lineage=f"sidecar:{case_id}:systemctl",
    )
    add(
        "REF-0011",
        "journal_excerpt",
        raw=raw_capture["journal_excerpt"],
        lineage=f"sidecar:{case_id}:journal",
    )

    synthesis = synthesize_propagation_evidence(candidate, (record,))
    add(
        "REF-0020",
        "candidate",
        full=candidate.to_dict(),
        lineage=f"candidate:{candidate.candidate_id}:full",
    )
    add(
        "REF-0021",
        "controlled_coverage",
        full=coverage.to_dict(),
        lineage=f"coverage:{coverage.evidence_id}:full",
    )
    if record.pairwise_evidence is not None:
        add(
            "REF-0022",
            "pairwise_evidence",
            full=record.pairwise_evidence.to_dict(),
            lineage=f"pairwise:{record.pairwise_evidence.evidence_id}:full",
        )
    add(
        "REF-0023",
        "controlled_record",
        full=record.to_dict(),
        lineage=f"record:{record.record_id}:full",
    )
    add(
        "REF-0024",
        "live_run",
        full=live_run.to_dict(),
        lineage=f"live-run:{record.record_id}:full",
    )
    add(
        "REF-0025",
        "protocol_execution",
        full=execution.to_dict(),
        lineage=f"execution:{execution.execution_id}:full",
    )
    add(
        "REF-0026",
        "synthesis",
        full=synthesis.to_dict(),
        lineage=f"synthesis:{synthesis.synthesis_id}:full",
    )

    source = CaseSource(
        case_id=case_id,
        task=(
            "Assess whether the evidence supports an observed downstream effect, "
            "bounded negative evidence, counterevidence, insufficient evidence, "
            "or ambiguity."
        ),
        environment=environment_copy,
        evidence_items=tuple(items),
    )

    supporting = set(refs)
    gold = CaseGold(
        case_id=case_id,
        classification=classification,
        must_abstain=False,
        supporting_evidence_refs=tuple(sorted(supporting)),
        invalid_evidence_refs=(),
        counterevidence_refs=(),
        maximum_allowed_causal_strength=(
            "hypothesis" if classification == "EFFECT_OBSERVED" else "none"
        ),
        origin="empirical",
        source_run_group=_source_run_group(case_id),
    )
    return CorpusCase(
        source=source,
        gold=gold,
        scenario_family=family,
        lineage_by_ref=refs,
    )


def _validate_raw_capture(
    execution: ControlledPropagationProtocolBoundExecution,
    raw_capture: Mapping[str, object],
) -> None:
    capture_value = _json_copy(raw_capture, field_name="raw_capture")
    if not isinstance(capture_value, dict):
        raise AssertionError("raw_capture JSON copy must remain a mapping")
    capture = cast(dict[str, object], capture_value)
    if capture.get("schema_version") != PHASE5F_SIDECAR_SCHEMA_VERSION:
        raise ValueError("raw capture schema_version mismatch")
    if capture.get("boot_id") != execution.attempt.boot_id:
        raise ValueError("raw capture boot_id must match bound execution")
    live = execution.live_run
    if capture.get("source_unit") != live.pair_artifact.source_unit:
        raise ValueError("raw capture source_unit mismatch")
    if capture.get("dependent_unit") != live.pair_artifact.dependent_unit:
        raise ValueError("raw capture dependent_unit mismatch")
    start = _required_nonnegative_int(capture, "started_monotonic_usec")
    end = _required_nonnegative_int(capture, "ended_monotonic_usec")
    ground = live.experiment_record.injection_outcome.ground_truth
    if ground.ended_monotonic_usec is None:
        raise ValueError("empirical ground truth must be closed")
    if start > ground.started_monotonic_usec or end < ground.ended_monotonic_usec:
        raise ValueError("raw capture must span the controlled fault interval")
    samples = capture.get("systemctl_samples")
    if not isinstance(samples, list) or len(samples) < 4:
        raise ValueError("raw capture requires bounded systemctl samples")
    sample_times: list[int] = []
    for sample in samples:
        if not isinstance(sample, Mapping):
            raise ValueError("raw capture systemctl samples must be mappings")
        sample_usec = sample.get("captured_monotonic_usec")
        if type(sample_usec) is not int or sample_usec < 0:
            raise ValueError("raw capture sample monotonic time is invalid")
        sample_times.append(sample_usec)
    if min(sample_times) > ground.started_monotonic_usec:
        raise ValueError("raw sidecar samples do not begin before fault start")
    if max(sample_times) < ground.ended_monotonic_usec:
        raise ValueError("raw sidecar samples do not extend through fault end")
    if capture.get("sidecar_error_count") != 0:
        raise ValueError("raw capture contains sidecar command errors")
    journal = capture.get("journal_excerpt")
    if not isinstance(journal, Mapping):
        raise ValueError("raw capture requires journal_excerpt mapping")


def _source_run_group(case_id: str) -> str:
    return f"RUN-{int(case_id[5:]):04d}"
