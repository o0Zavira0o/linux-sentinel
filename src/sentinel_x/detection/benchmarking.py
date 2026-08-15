"""Repeated statistical benchmarking for frozen systemd detection evaluation.

Phase 4B-B aggregates repeated Phase 4B-A live evaluations.  It deliberately
keeps detector correctness, transition measurability, healthy-control false
positives, and timing missingness as separate dimensions.  No fault mutation
or service control is implemented here; execution remains delegated to the
frozen evaluation and Phase 3 laboratory layers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final, Protocol, Sequence

from sentinel_x.detection.evaluation import (
    FaultDetectionEvaluationRun,
    SystemdFaultDetectionEvaluator,
)
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    SystemdServiceHealthStatus,
)
from sentinel_x.lab.fixture import (
    SystemdLabFixtureArtifact,
    validate_lab_fixture_unit_name,
)
from sentinel_x.lab.models import FaultExperimentManifest, FaultMode, FaultScenario

_DEFAULT_REPETITIONS_PER_MODE: Final[int] = 5
_MIN_REPETITIONS_PER_MODE: Final[int] = 1
_MAX_REPETITIONS_PER_MODE: Final[int] = 32
_MAX_TOTAL_RUNS: Final[int] = 64
_MAX_TIMEOUT_SECONDS: Final[float] = 300.0


class RepeatedDetectionBenchmarkError(RuntimeError):
    """Base error for repeated detector benchmarking."""


class RepeatedDetectionBenchmarkContractError(RepeatedDetectionBenchmarkError):
    """Raised when repeated benchmark inputs or aggregates are inconsistent."""


class DetectionEvaluationRunner(Protocol):
    """Structural dependency for one frozen Phase 4B-A evaluation."""

    def run(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultDetectionEvaluationRun:
        """Execute one ground-truth-aligned detector evaluation."""

        ...


@dataclass(frozen=True, slots=True)
class RepeatedDetectionBenchmarkPlan:
    """Immutable balanced plan for repeated inactive/failed evaluation."""

    target_unit: str
    repetitions_per_mode: int = _DEFAULT_REPETITIONS_PER_MODE
    evidence_grace_seconds: float = 2.0
    baseline_timeout_seconds: float = 10.0
    fault_timeout_seconds: float = 10.0
    recovery_timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        validate_lab_fixture_unit_name(self.target_unit)
        if isinstance(self.repetitions_per_mode, bool) or not isinstance(
            self.repetitions_per_mode,
            int,
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "repetitions_per_mode must be an integer"
            )
        if not (
            _MIN_REPETITIONS_PER_MODE
            <= self.repetitions_per_mode
            <= _MAX_REPETITIONS_PER_MODE
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "repetitions_per_mode is outside the safety bound"
            )
        if self.expected_run_count > _MAX_TOTAL_RUNS:
            raise RepeatedDetectionBenchmarkContractError(
                "repeated benchmark run count exceeds the safety bound"
            )
        for field_name in (
            "evidence_grace_seconds",
            "baseline_timeout_seconds",
            "fault_timeout_seconds",
            "recovery_timeout_seconds",
        ):
            normalized = _validate_positive_seconds(
                getattr(self, field_name),
                field_name=field_name,
            )
            object.__setattr__(self, field_name, normalized)
        if self.evidence_grace_seconds > self.recovery_timeout_seconds:
            raise RepeatedDetectionBenchmarkContractError(
                "evidence_grace_seconds must not exceed recovery_timeout_seconds"
            )

    @property
    def expected_run_count(self) -> int:
        """Return the exact balanced run count implied by this plan."""

        return self.repetitions_per_mode * 2

    def case_sequence(self) -> tuple[tuple[int, FaultMode, str], ...]:
        """Return deterministic alternating inactive/failed scenario cases."""

        cases: list[tuple[int, FaultMode, str]] = []
        ordinal = 1
        for repetition in range(1, self.repetitions_per_mode + 1):
            for mode, label in (
                (FaultMode.SERVICE_INACTIVE, "inactive"),
                (FaultMode.SERVICE_FAILED, "failed"),
            ):
                cases.append(
                    (
                        ordinal,
                        mode,
                        f"detectbench-{label}-r{repetition:03d}",
                    )
                )
                ordinal += 1
        return tuple(cases)

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly repeated benchmark plan."""

        return {
            "target_unit": self.target_unit,
            "repetitions_per_mode": self.repetitions_per_mode,
            "expected_run_count": self.expected_run_count,
            "evidence_grace_seconds": self.evidence_grace_seconds,
            "baseline_timeout_seconds": self.baseline_timeout_seconds,
            "fault_timeout_seconds": self.fault_timeout_seconds,
            "recovery_timeout_seconds": self.recovery_timeout_seconds,
            "case_sequence": [
                {
                    "ordinal": ordinal,
                    "fault_mode": mode.value,
                    "scenario_id": scenario_id,
                }
                for ordinal, mode, scenario_id in self.case_sequence()
            ],
        }


@dataclass(frozen=True, slots=True)
class DetectionIntegerMetricSummary:
    """Nearest-rank integer p50/p95 distribution with explicit missingness."""

    observed_count: int
    missing_count: int
    minimum: int | None
    p50: int | None
    p95: int | None
    maximum: int | None

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.observed_count, field_name="observed_count")
        _validate_nonnegative_int(self.missing_count, field_name="missing_count")
        if self.observed_count == 0:
            if any(
                value is not None
                for value in (self.minimum, self.p50, self.p95, self.maximum)
            ):
                raise RepeatedDetectionBenchmarkContractError(
                    "empty metric summaries must preserve missing distribution values"
                )
            return
        if any(
            value is None for value in (self.minimum, self.p50, self.p95, self.maximum)
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "observed metric summaries require all distribution values"
            )
        values = tuple(
            value
            for value in (self.minimum, self.p50, self.p95, self.maximum)
            if value is not None
        )
        for index, value in enumerate(values):
            _validate_int(value, field_name=f"distribution_value_{index}")
        if values != tuple(sorted(values)):
            raise RepeatedDetectionBenchmarkContractError(
                "metric distribution values must be monotonic"
            )

    @classmethod
    def from_values(
        cls,
        values: Sequence[int | None],
        *,
        require_nonnegative: bool = False,
    ) -> DetectionIntegerMetricSummary:
        """Summarize integer values without fabricating missing observations."""

        observed: list[int] = []
        missing = 0
        for value in values:
            if value is None:
                missing += 1
                continue
            _validate_int(value, field_name="metric_value")
            if require_nonnegative and value < 0:
                raise RepeatedDetectionBenchmarkContractError(
                    "latency metric values must be non-negative"
                )
            observed.append(value)
        observed.sort()
        if not observed:
            return cls(0, missing, None, None, None, None)
        p50_index = max(0, math.ceil(0.50 * len(observed)) - 1)
        p95_index = max(0, math.ceil(0.95 * len(observed)) - 1)
        return cls(
            observed_count=len(observed),
            missing_count=missing,
            minimum=observed[0],
            p50=observed[p50_index],
            p95=observed[p95_index],
            maximum=observed[-1],
        )

    def to_dict(self) -> dict[str, int | None]:
        """Return a stable JSON-friendly metric summary."""

        return {
            "observed_count": self.observed_count,
            "missing_count": self.missing_count,
            "minimum": self.minimum,
            "p50": self.p50,
            "p95": self.p95,
            "maximum": self.maximum,
        }


@dataclass(frozen=True, slots=True)
class RepeatedDetectionBenchmarkRecord:
    """One repeated evaluation plus explicit ground-truth-active projections."""

    ordinal: int
    fault_mode: FaultMode
    scenario_id: str
    evaluation_run: FaultDetectionEvaluationRun
    ground_truth_active_sample_count: int
    ground_truth_fault_state_sample_count: int
    ground_truth_correct_fault_state_detection_count: int
    ground_truth_missed_fault_state_sample_count: int
    ground_truth_unassessed_count: int
    ground_truth_other_anomaly_count: int

    def __post_init__(self) -> None:
        _validate_positive_int(self.ordinal, field_name="ordinal")
        if not isinstance(self.fault_mode, FaultMode):
            raise RepeatedDetectionBenchmarkContractError(
                "fault_mode must be a FaultMode"
            )
        _validate_nonempty_text(self.scenario_id, field_name="scenario_id")
        if not isinstance(self.evaluation_run, FaultDetectionEvaluationRun):
            raise RepeatedDetectionBenchmarkContractError(
                "evaluation_run must be a FaultDetectionEvaluationRun"
            )
        benchmark = self.evaluation_run.benchmark
        if benchmark.fault_mode is not self.fault_mode:
            raise RepeatedDetectionBenchmarkContractError(
                "record fault mode must match the evaluation benchmark"
            )
        if benchmark.scenario_id != self.scenario_id:
            raise RepeatedDetectionBenchmarkContractError(
                "record scenario must match the evaluation benchmark"
            )
        for field_name in (
            "ground_truth_active_sample_count",
            "ground_truth_fault_state_sample_count",
            "ground_truth_correct_fault_state_detection_count",
            "ground_truth_missed_fault_state_sample_count",
            "ground_truth_unassessed_count",
            "ground_truth_other_anomaly_count",
        ):
            _validate_nonnegative_int(getattr(self, field_name), field_name=field_name)
        if (
            self.ground_truth_fault_state_sample_count
            > self.ground_truth_active_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "ground-truth fault-state samples cannot exceed active-window samples"
            )
        if (
            self.ground_truth_correct_fault_state_detection_count
            + self.ground_truth_missed_fault_state_sample_count
            != self.ground_truth_fault_state_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "ground-truth fault-state classification counts must partition samples"
            )
        if self.ground_truth_unassessed_count > self.ground_truth_active_sample_count:
            raise RepeatedDetectionBenchmarkContractError(
                "ground-truth unassessed count exceeds active-window samples"
            )
        if (
            self.ground_truth_other_anomaly_count
            > self.ground_truth_active_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "ground-truth other-anomaly count exceeds active-window samples"
            )

    @property
    def experiment_id(self) -> str:
        """Return the underlying unique experiment identity."""

        return self.evaluation_run.benchmark.experiment_id

    @property
    def ground_truth_active_observed(self) -> bool:
        """Return whether at least one detector sample fell in ground-truth time."""

        return self.ground_truth_active_sample_count > 0

    def to_dict(self) -> dict[str, object]:
        """Return bounded record metrics without serializing raw sample streams."""

        return {
            "ordinal": self.ordinal,
            "fault_mode": self.fault_mode.value,
            "scenario_id": self.scenario_id,
            "experiment_id": self.experiment_id,
            "detection_benchmark": self.evaluation_run.benchmark.to_dict(),
            "ground_truth_active_sample_count": self.ground_truth_active_sample_count,
            "ground_truth_fault_state_sample_count": (
                self.ground_truth_fault_state_sample_count
            ),
            "ground_truth_correct_fault_state_detection_count": (
                self.ground_truth_correct_fault_state_detection_count
            ),
            "ground_truth_missed_fault_state_sample_count": (
                self.ground_truth_missed_fault_state_sample_count
            ),
            "ground_truth_unassessed_count": self.ground_truth_unassessed_count,
            "ground_truth_other_anomaly_count": self.ground_truth_other_anomaly_count,
            "ground_truth_active_observed": self.ground_truth_active_observed,
        }


@dataclass(frozen=True, slots=True)
class DetectionBenchmarkScopeReport:
    """Aggregate detector results for one overall or per-mode scope."""

    run_count: int
    detection_covered_run_count: int
    classification_complete_run_count: int
    healthy_control_complete_run_count: int
    healthy_control_clean_run_count: int
    ground_truth_active_observed_run_count: int
    transition_reported_run_count: int
    transition_latency_measurable_run_count: int
    total_detection_sample_count: int
    total_fault_window_sample_count: int
    total_fault_state_sample_count: int
    total_correct_fault_state_detection_count: int
    total_missed_fault_state_sample_count: int
    total_fault_window_unassessed_count: int
    total_fault_window_other_anomaly_count: int
    total_healthy_control_sample_count: int
    total_healthy_control_false_positive_count: int
    total_ground_truth_active_sample_count: int
    total_ground_truth_fault_state_sample_count: int
    total_ground_truth_correct_fault_state_detection_count: int
    total_ground_truth_missed_fault_state_sample_count: int
    total_ground_truth_unassessed_count: int
    total_ground_truth_other_anomaly_count: int
    detection_visibility_latency_usec: DetectionIntegerMetricSummary
    detection_transition_offset_usec: DetectionIntegerMetricSummary
    ground_truth_confirmation_offset_usec: DetectionIntegerMetricSummary
    recovery_detection_latency_usec: DetectionIntegerMetricSummary

    def __post_init__(self) -> None:
        integer_fields = (
            "run_count",
            "detection_covered_run_count",
            "classification_complete_run_count",
            "healthy_control_complete_run_count",
            "healthy_control_clean_run_count",
            "ground_truth_active_observed_run_count",
            "transition_reported_run_count",
            "transition_latency_measurable_run_count",
            "total_detection_sample_count",
            "total_fault_window_sample_count",
            "total_fault_state_sample_count",
            "total_correct_fault_state_detection_count",
            "total_missed_fault_state_sample_count",
            "total_fault_window_unassessed_count",
            "total_fault_window_other_anomaly_count",
            "total_healthy_control_sample_count",
            "total_healthy_control_false_positive_count",
            "total_ground_truth_active_sample_count",
            "total_ground_truth_fault_state_sample_count",
            "total_ground_truth_correct_fault_state_detection_count",
            "total_ground_truth_missed_fault_state_sample_count",
            "total_ground_truth_unassessed_count",
            "total_ground_truth_other_anomaly_count",
        )
        for field_name in integer_fields:
            _validate_nonnegative_int(getattr(self, field_name), field_name=field_name)
        for count_name in (
            "detection_covered_run_count",
            "classification_complete_run_count",
            "healthy_control_complete_run_count",
            "healthy_control_clean_run_count",
            "ground_truth_active_observed_run_count",
            "transition_reported_run_count",
            "transition_latency_measurable_run_count",
        ):
            if getattr(self, count_name) > self.run_count:
                raise RepeatedDetectionBenchmarkContractError(
                    f"{count_name} cannot exceed run_count"
                )
        if (
            self.healthy_control_clean_run_count
            > self.healthy_control_complete_run_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "clean healthy-control runs cannot exceed complete healthy-control runs"
            )
        if (
            self.transition_latency_measurable_run_count
            > self.transition_reported_run_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "measurable transition latency cannot exceed reported transitions"
            )
        if (
            self.total_correct_fault_state_detection_count
            + self.total_missed_fault_state_sample_count
            != self.total_fault_state_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "fault-state aggregate classification counts must partition samples"
            )
        if self.total_fault_state_sample_count > self.total_fault_window_sample_count:
            raise RepeatedDetectionBenchmarkContractError(
                "fault-state samples cannot exceed fault-window samples"
            )
        if (
            self.total_fault_window_unassessed_count
            > self.total_fault_window_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "fault-window unassessed count exceeds fault-window samples"
            )
        if (
            self.total_healthy_control_false_positive_count
            > self.total_healthy_control_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "healthy-control false positives exceed control samples"
            )
        if (
            self.total_ground_truth_correct_fault_state_detection_count
            + self.total_ground_truth_missed_fault_state_sample_count
            != self.total_ground_truth_fault_state_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "ground-truth fault-state classification counts must partition samples"
            )
        if (
            self.total_ground_truth_fault_state_sample_count
            > self.total_ground_truth_active_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "ground-truth fault-state samples exceed active-window samples"
            )
        if (
            self.total_ground_truth_unassessed_count
            > self.total_ground_truth_active_sample_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "ground-truth unassessed count exceeds active-window samples"
            )
        for field_name in (
            "detection_visibility_latency_usec",
            "detection_transition_offset_usec",
            "ground_truth_confirmation_offset_usec",
            "recovery_detection_latency_usec",
        ):
            if not isinstance(getattr(self, field_name), DetectionIntegerMetricSummary):
                raise RepeatedDetectionBenchmarkContractError(
                    f"{field_name} must be a DetectionIntegerMetricSummary"
                )
        if (
            self.detection_visibility_latency_usec.observed_count
            != self.transition_latency_measurable_run_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "visibility latency observed count must match measurable-run count"
            )
        if (
            self.detection_visibility_latency_usec.observed_count
            + self.detection_visibility_latency_usec.missing_count
            != self.run_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "visibility latency summary must cover every run"
            )
        for summary in (
            self.detection_transition_offset_usec,
            self.ground_truth_confirmation_offset_usec,
            self.recovery_detection_latency_usec,
        ):
            if summary.observed_count + summary.missing_count != self.run_count:
                raise RepeatedDetectionBenchmarkContractError(
                    "run-level metric summaries must cover every run"
                )

    @property
    def detection_coverage_rate(self) -> float:
        return _rate(self.detection_covered_run_count, self.run_count)

    @property
    def classification_complete_rate(self) -> float:
        return _rate(self.classification_complete_run_count, self.run_count)

    @property
    def healthy_control_complete_rate(self) -> float:
        return _rate(self.healthy_control_complete_run_count, self.run_count)

    @property
    def healthy_control_clean_rate(self) -> float:
        return _rate(self.healthy_control_clean_run_count, self.run_count)

    @property
    def ground_truth_active_observed_rate(self) -> float:
        return _rate(self.ground_truth_active_observed_run_count, self.run_count)

    @property
    def transition_reported_rate(self) -> float:
        return _rate(self.transition_reported_run_count, self.run_count)

    @property
    def transition_latency_measurable_rate(self) -> float:
        return _rate(self.transition_latency_measurable_run_count, self.run_count)

    @property
    def fault_state_sample_accuracy(self) -> float | None:
        return _optional_rate(
            self.total_correct_fault_state_detection_count,
            self.total_fault_state_sample_count,
        )

    @property
    def fault_window_unassessed_rate(self) -> float | None:
        return _optional_rate(
            self.total_fault_window_unassessed_count,
            self.total_fault_window_sample_count,
        )

    @property
    def healthy_control_false_positive_rate(self) -> float | None:
        return _optional_rate(
            self.total_healthy_control_false_positive_count,
            self.total_healthy_control_sample_count,
        )

    @property
    def ground_truth_fault_state_sample_accuracy(self) -> float | None:
        return _optional_rate(
            self.total_ground_truth_correct_fault_state_detection_count,
            self.total_ground_truth_fault_state_sample_count,
        )

    @property
    def ground_truth_unassessed_rate(self) -> float | None:
        return _optional_rate(
            self.total_ground_truth_unassessed_count,
            self.total_ground_truth_active_sample_count,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly aggregate report."""

        return {
            "run_count": self.run_count,
            "detection_covered_run_count": self.detection_covered_run_count,
            "detection_coverage_rate": self.detection_coverage_rate,
            "classification_complete_run_count": self.classification_complete_run_count,
            "classification_complete_rate": self.classification_complete_rate,
            "healthy_control_complete_run_count": (
                self.healthy_control_complete_run_count
            ),
            "healthy_control_complete_rate": self.healthy_control_complete_rate,
            "healthy_control_clean_run_count": self.healthy_control_clean_run_count,
            "healthy_control_clean_rate": self.healthy_control_clean_rate,
            "ground_truth_active_observed_run_count": (
                self.ground_truth_active_observed_run_count
            ),
            "ground_truth_active_observed_rate": self.ground_truth_active_observed_rate,
            "transition_reported_run_count": self.transition_reported_run_count,
            "transition_reported_rate": self.transition_reported_rate,
            "transition_latency_measurable_run_count": (
                self.transition_latency_measurable_run_count
            ),
            "transition_latency_measurable_rate": (
                self.transition_latency_measurable_rate
            ),
            "total_detection_sample_count": self.total_detection_sample_count,
            "total_fault_window_sample_count": self.total_fault_window_sample_count,
            "total_fault_state_sample_count": self.total_fault_state_sample_count,
            "total_correct_fault_state_detection_count": (
                self.total_correct_fault_state_detection_count
            ),
            "total_missed_fault_state_sample_count": (
                self.total_missed_fault_state_sample_count
            ),
            "fault_state_sample_accuracy": self.fault_state_sample_accuracy,
            "total_fault_window_unassessed_count": (
                self.total_fault_window_unassessed_count
            ),
            "fault_window_unassessed_rate": self.fault_window_unassessed_rate,
            "total_fault_window_other_anomaly_count": (
                self.total_fault_window_other_anomaly_count
            ),
            "total_healthy_control_sample_count": (
                self.total_healthy_control_sample_count
            ),
            "total_healthy_control_false_positive_count": (
                self.total_healthy_control_false_positive_count
            ),
            "healthy_control_false_positive_rate": (
                self.healthy_control_false_positive_rate
            ),
            "total_ground_truth_active_sample_count": (
                self.total_ground_truth_active_sample_count
            ),
            "total_ground_truth_fault_state_sample_count": (
                self.total_ground_truth_fault_state_sample_count
            ),
            "total_ground_truth_correct_fault_state_detection_count": (
                self.total_ground_truth_correct_fault_state_detection_count
            ),
            "total_ground_truth_missed_fault_state_sample_count": (
                self.total_ground_truth_missed_fault_state_sample_count
            ),
            "ground_truth_fault_state_sample_accuracy": (
                self.ground_truth_fault_state_sample_accuracy
            ),
            "total_ground_truth_unassessed_count": (
                self.total_ground_truth_unassessed_count
            ),
            "ground_truth_unassessed_rate": self.ground_truth_unassessed_rate,
            "total_ground_truth_other_anomaly_count": (
                self.total_ground_truth_other_anomaly_count
            ),
            "detection_visibility_latency_usec": (
                self.detection_visibility_latency_usec.to_dict()
            ),
            "detection_transition_offset_usec": (
                self.detection_transition_offset_usec.to_dict()
            ),
            "ground_truth_confirmation_offset_usec": (
                self.ground_truth_confirmation_offset_usec.to_dict()
            ),
            "recovery_detection_latency_usec": (
                self.recovery_detection_latency_usec.to_dict()
            ),
        }


@dataclass(frozen=True, slots=True)
class RepeatedDetectionBenchmarkReport:
    """Overall plus per-fault-mode statistical report."""

    overall: DetectionBenchmarkScopeReport
    service_inactive: DetectionBenchmarkScopeReport
    service_failed: DetectionBenchmarkScopeReport

    def __post_init__(self) -> None:
        for field_name in ("overall", "service_inactive", "service_failed"):
            if not isinstance(getattr(self, field_name), DetectionBenchmarkScopeReport):
                raise RepeatedDetectionBenchmarkContractError(
                    f"{field_name} must be a DetectionBenchmarkScopeReport"
                )
        if (
            self.service_inactive.run_count + self.service_failed.run_count
            != self.overall.run_count
        ):
            raise RepeatedDetectionBenchmarkContractError(
                "per-mode run counts must sum to the overall run count"
            )

    def to_dict(self) -> dict[str, object]:
        """Return a stable overall and per-mode report."""

        return {
            "overall": self.overall.to_dict(),
            "service_inactive": self.service_inactive.to_dict(),
            "service_failed": self.service_failed.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RepeatedDetectionBenchmark:
    """Complete balanced repeated benchmark with bounded record projections."""

    plan: RepeatedDetectionBenchmarkPlan
    records: tuple[RepeatedDetectionBenchmarkRecord, ...]
    report: RepeatedDetectionBenchmarkReport

    def __post_init__(self) -> None:
        if not isinstance(self.plan, RepeatedDetectionBenchmarkPlan):
            raise RepeatedDetectionBenchmarkContractError(
                "plan must be a RepeatedDetectionBenchmarkPlan"
            )
        if not isinstance(self.records, tuple):
            raise RepeatedDetectionBenchmarkContractError("records must be a tuple")
        if len(self.records) != self.plan.expected_run_count:
            raise RepeatedDetectionBenchmarkContractError(
                "record count must match the repeated benchmark plan"
            )
        expected_cases = self.plan.case_sequence()
        experiment_ids: set[str] = set()
        for record, (ordinal, mode, scenario_id) in zip(
            self.records,
            expected_cases,
            strict=True,
        ):
            if not isinstance(record, RepeatedDetectionBenchmarkRecord):
                raise RepeatedDetectionBenchmarkContractError(
                    "records must contain typed repeated benchmark records"
                )
            if (
                record.ordinal != ordinal
                or record.fault_mode is not mode
                or record.scenario_id != scenario_id
            ):
                raise RepeatedDetectionBenchmarkContractError(
                    "records must preserve the exact deterministic plan sequence"
                )
            if record.experiment_id in experiment_ids:
                raise RepeatedDetectionBenchmarkContractError(
                    "repeated benchmark experiment identities must be unique"
                )
            experiment_ids.add(record.experiment_id)
        if not isinstance(self.report, RepeatedDetectionBenchmarkReport):
            raise RepeatedDetectionBenchmarkContractError(
                "report must be a RepeatedDetectionBenchmarkReport"
            )
        expected_report = build_repeated_detection_report(self.records)
        if self.report != expected_report:
            raise RepeatedDetectionBenchmarkContractError(
                "report does not match repeated benchmark records"
            )

    def to_dict(self) -> dict[str, object]:
        """Return bounded benchmark metadata, records, and report."""

        return {
            "plan": self.plan.to_dict(),
            "records": [record.to_dict() for record in self.records],
            "report": self.report.to_dict(),
        }


class SystemdRepeatedDetectionBenchmarkRunner:
    """Execute a deterministic balanced sequence through frozen 4B-A evaluation."""

    def __init__(self, evaluator: DetectionEvaluationRunner | None = None) -> None:
        self._evaluator: DetectionEvaluationRunner = (
            SystemdFaultDetectionEvaluator() if evaluator is None else evaluator
        )
        if not callable(getattr(self._evaluator, "run", None)):
            raise TypeError("evaluator must expose a callable run method")

    def run(
        self,
        plan: RepeatedDetectionBenchmarkPlan,
        artifact: SystemdLabFixtureArtifact,
    ) -> RepeatedDetectionBenchmark:
        """Execute all planned evaluations and aggregate their detector outcomes."""

        if not isinstance(plan, RepeatedDetectionBenchmarkPlan):
            raise RepeatedDetectionBenchmarkContractError(
                "plan must be a RepeatedDetectionBenchmarkPlan"
            )
        if not isinstance(artifact, SystemdLabFixtureArtifact):
            raise RepeatedDetectionBenchmarkContractError(
                "artifact must be a SystemdLabFixtureArtifact"
            )
        if artifact.unit_name != plan.target_unit:
            raise RepeatedDetectionBenchmarkContractError(
                "plan target must match the lab fixture artifact"
            )
        records: list[RepeatedDetectionBenchmarkRecord] = []
        for ordinal, mode, scenario_id in plan.case_sequence():
            manifest = FaultExperimentManifest(
                FaultScenario(
                    scenario_id=scenario_id,
                    description=(
                        "Sentinel-X repeated ground-truth-aligned detection benchmark."
                    ),
                    target_unit=plan.target_unit,
                    fault_mode=mode,
                    evidence_grace_seconds=plan.evidence_grace_seconds,
                    baseline_timeout_seconds=plan.baseline_timeout_seconds,
                    fault_timeout_seconds=plan.fault_timeout_seconds,
                    recovery_timeout_seconds=plan.recovery_timeout_seconds,
                )
            )
            evaluation_run = self._evaluator.run(manifest, artifact)
            benchmark = evaluation_run.benchmark
            if benchmark.scenario_id != scenario_id:
                raise RepeatedDetectionBenchmarkContractError(
                    "evaluator returned the wrong scenario identity"
                )
            if benchmark.fault_mode is not mode:
                raise RepeatedDetectionBenchmarkContractError(
                    "evaluator returned the wrong fault mode"
                )
            if benchmark.target_unit != plan.target_unit:
                raise RepeatedDetectionBenchmarkContractError(
                    "evaluator returned the wrong target unit"
                )
            records.append(
                build_repeated_detection_record(
                    ordinal=ordinal,
                    fault_mode=mode,
                    scenario_id=scenario_id,
                    evaluation_run=evaluation_run,
                )
            )
        typed_records = tuple(records)
        report = build_repeated_detection_report(typed_records)
        return RepeatedDetectionBenchmark(
            plan=plan,
            records=typed_records,
            report=report,
        )


def build_repeated_detection_record(
    *,
    ordinal: int,
    fault_mode: FaultMode,
    scenario_id: str,
    evaluation_run: FaultDetectionEvaluationRun,
) -> RepeatedDetectionBenchmarkRecord:
    """Project one 4B-A run onto explicit ground-truth-active interval metrics."""

    if not isinstance(evaluation_run, FaultDetectionEvaluationRun):
        raise RepeatedDetectionBenchmarkContractError(
            "evaluation_run must be a FaultDetectionEvaluationRun"
        )
    benchmark = evaluation_run.benchmark
    if benchmark.fault_mode is not fault_mode:
        raise RepeatedDetectionBenchmarkContractError(
            "fault_mode must match evaluation_run"
        )
    if benchmark.scenario_id != scenario_id:
        raise RepeatedDetectionBenchmarkContractError(
            "scenario_id must match evaluation_run"
        )
    expected_status, expected_class, expected_active_state = _expected_fault_mapping(
        fault_mode
    )
    ground_truth_samples = tuple(
        sample
        for sample in evaluation_run.detection_samples
        if benchmark.ground_truth_started_monotonic_usec
        <= sample.assessed_monotonic_usec
        <= benchmark.ground_truth_ended_monotonic_usec
    )
    ground_truth_fault_state_samples = tuple(
        sample
        for sample in ground_truth_samples
        if sample.active_state == expected_active_state
    )
    correct_fault_state_samples = tuple(
        sample
        for sample in ground_truth_fault_state_samples
        if sample.status is expected_status and sample.anomaly_class is expected_class
    )
    unassessed_samples = tuple(
        sample
        for sample in ground_truth_samples
        if sample.status is SystemdServiceHealthStatus.UNASSESSED
    )
    other_anomalies = tuple(
        sample
        for sample in ground_truth_samples
        if sample.is_anomalous and sample.anomaly_class is not expected_class
    )
    return RepeatedDetectionBenchmarkRecord(
        ordinal=ordinal,
        fault_mode=fault_mode,
        scenario_id=scenario_id,
        evaluation_run=evaluation_run,
        ground_truth_active_sample_count=len(ground_truth_samples),
        ground_truth_fault_state_sample_count=len(ground_truth_fault_state_samples),
        ground_truth_correct_fault_state_detection_count=len(
            correct_fault_state_samples
        ),
        ground_truth_missed_fault_state_sample_count=(
            len(ground_truth_fault_state_samples) - len(correct_fault_state_samples)
        ),
        ground_truth_unassessed_count=len(unassessed_samples),
        ground_truth_other_anomaly_count=len(other_anomalies),
    )


def build_repeated_detection_report(
    records: tuple[RepeatedDetectionBenchmarkRecord, ...],
) -> RepeatedDetectionBenchmarkReport:
    """Build overall and per-mode reports from exact repeated benchmark records."""

    if not isinstance(records, tuple):
        raise RepeatedDetectionBenchmarkContractError("records must be a tuple")
    if not records:
        raise RepeatedDetectionBenchmarkContractError(
            "at least one repeated benchmark record is required"
        )
    for record in records:
        if not isinstance(record, RepeatedDetectionBenchmarkRecord):
            raise RepeatedDetectionBenchmarkContractError(
                "records must contain RepeatedDetectionBenchmarkRecord values"
            )
    inactive = tuple(
        record for record in records if record.fault_mode is FaultMode.SERVICE_INACTIVE
    )
    failed = tuple(
        record for record in records if record.fault_mode is FaultMode.SERVICE_FAILED
    )
    return RepeatedDetectionBenchmarkReport(
        overall=_build_scope_report(records),
        service_inactive=_build_scope_report(inactive),
        service_failed=_build_scope_report(failed),
    )


def _build_scope_report(
    records: tuple[RepeatedDetectionBenchmarkRecord, ...],
) -> DetectionBenchmarkScopeReport:
    benchmarks = tuple(record.evaluation_run.benchmark for record in records)
    run_count = len(records)
    healthy_control_samples = sum(
        benchmark.pre_fault_sample_count + benchmark.post_recovery_sample_count
        for benchmark in benchmarks
    )
    return DetectionBenchmarkScopeReport(
        run_count=run_count,
        detection_covered_run_count=sum(
            benchmark.detection_coverage for benchmark in benchmarks
        ),
        classification_complete_run_count=sum(
            benchmark.fault_state_classification_complete for benchmark in benchmarks
        ),
        healthy_control_complete_run_count=sum(
            benchmark.healthy_control_complete for benchmark in benchmarks
        ),
        healthy_control_clean_run_count=sum(
            benchmark.healthy_control_clean for benchmark in benchmarks
        ),
        ground_truth_active_observed_run_count=sum(
            record.ground_truth_active_observed for record in records
        ),
        transition_reported_run_count=sum(
            benchmark.reported_fault_transition_monotonic_usec is not None
            for benchmark in benchmarks
        ),
        transition_latency_measurable_run_count=sum(
            benchmark.detection_visibility_latency_usec is not None
            for benchmark in benchmarks
        ),
        total_detection_sample_count=sum(
            benchmark.detection_sample_count for benchmark in benchmarks
        ),
        total_fault_window_sample_count=sum(
            benchmark.fault_window_sample_count for benchmark in benchmarks
        ),
        total_fault_state_sample_count=sum(
            benchmark.fault_state_sample_count for benchmark in benchmarks
        ),
        total_correct_fault_state_detection_count=sum(
            benchmark.correct_fault_state_detection_count for benchmark in benchmarks
        ),
        total_missed_fault_state_sample_count=sum(
            benchmark.missed_fault_state_sample_count for benchmark in benchmarks
        ),
        total_fault_window_unassessed_count=sum(
            benchmark.fault_window_unassessed_count for benchmark in benchmarks
        ),
        total_fault_window_other_anomaly_count=sum(
            benchmark.fault_window_other_anomaly_count for benchmark in benchmarks
        ),
        total_healthy_control_sample_count=healthy_control_samples,
        total_healthy_control_false_positive_count=sum(
            benchmark.pre_fault_false_positive_count
            + benchmark.post_recovery_false_positive_count
            for benchmark in benchmarks
        ),
        total_ground_truth_active_sample_count=sum(
            record.ground_truth_active_sample_count for record in records
        ),
        total_ground_truth_fault_state_sample_count=sum(
            record.ground_truth_fault_state_sample_count for record in records
        ),
        total_ground_truth_correct_fault_state_detection_count=sum(
            record.ground_truth_correct_fault_state_detection_count
            for record in records
        ),
        total_ground_truth_missed_fault_state_sample_count=sum(
            record.ground_truth_missed_fault_state_sample_count for record in records
        ),
        total_ground_truth_unassessed_count=sum(
            record.ground_truth_unassessed_count for record in records
        ),
        total_ground_truth_other_anomaly_count=sum(
            record.ground_truth_other_anomaly_count for record in records
        ),
        detection_visibility_latency_usec=DetectionIntegerMetricSummary.from_values(
            tuple(
                benchmark.detection_visibility_latency_usec for benchmark in benchmarks
            ),
            require_nonnegative=True,
        ),
        detection_transition_offset_usec=DetectionIntegerMetricSummary.from_values(
            tuple(
                benchmark.detection_transition_offset_usec for benchmark in benchmarks
            ),
        ),
        ground_truth_confirmation_offset_usec=DetectionIntegerMetricSummary.from_values(
            tuple(
                benchmark.ground_truth_confirmation_offset_usec
                for benchmark in benchmarks
            ),
        ),
        recovery_detection_latency_usec=DetectionIntegerMetricSummary.from_values(
            tuple(
                benchmark.recovery_detection_latency_usec for benchmark in benchmarks
            ),
            require_nonnegative=True,
        ),
    )


def _expected_fault_mapping(
    fault_mode: FaultMode,
) -> tuple[
    SystemdServiceHealthStatus,
    DetectionAnomalyClass,
    str,
]:
    if fault_mode is FaultMode.SERVICE_INACTIVE:
        return (
            SystemdServiceHealthStatus.INACTIVE,
            DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE,
            "inactive",
        )
    if fault_mode is FaultMode.SERVICE_FAILED:
        return (
            SystemdServiceHealthStatus.FAILED,
            DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
            "failed",
        )
    raise RepeatedDetectionBenchmarkContractError("unsupported fault mode")


def _rate(numerator: int, denominator: int) -> float:
    _validate_nonnegative_int(numerator, field_name="rate_numerator")
    _validate_nonnegative_int(denominator, field_name="rate_denominator")
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _optional_rate(numerator: int, denominator: int) -> float | None:
    _validate_nonnegative_int(numerator, field_name="rate_numerator")
    _validate_nonnegative_int(denominator, field_name="rate_denominator")
    if denominator == 0:
        return None
    return numerator / denominator


def _validate_positive_seconds(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RepeatedDetectionBenchmarkContractError(
            f"{field_name} must be a positive number"
        )
    normalized = float(value)
    if not math.isfinite(normalized) or not (0 < normalized <= _MAX_TIMEOUT_SECONDS):
        raise RepeatedDetectionBenchmarkContractError(
            f"{field_name} is outside the supported bound"
        )
    return normalized


def _validate_nonempty_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RepeatedDetectionBenchmarkContractError(
            f"{field_name} must be non-empty text"
        )


def _validate_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RepeatedDetectionBenchmarkContractError(
            f"{field_name} must be an integer"
        )
    return value


def _validate_nonnegative_int(value: object, *, field_name: str) -> None:
    normalized = _validate_int(value, field_name=field_name)
    if normalized < 0:
        raise RepeatedDetectionBenchmarkContractError(
            f"{field_name} must be non-negative"
        )


def _validate_positive_int(value: object, *, field_name: str) -> None:
    normalized = _validate_int(value, field_name=field_name)
    if normalized <= 0:
        raise RepeatedDetectionBenchmarkContractError(f"{field_name} must be positive")
