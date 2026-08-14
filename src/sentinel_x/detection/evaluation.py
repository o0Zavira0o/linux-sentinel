"""Ground-truth-aligned live evaluation for the conservative systemd detector.

Phase 4B deliberately evaluates the frozen Phase 4A detector against real
systemd observations collected during frozen Phase 3 fault experiments.  It
never reconstructs missing fields from the compact Phase 3E dataset, because
that would turn replay convenience into fabricated evidence.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Final, Protocol

from sentinel_x.core.events import SentinelEvent
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)
from sentinel_x.detection.systemd import SystemdServiceStateDetector
from sentinel_x.lab.fixture import SystemdLabFixtureArtifact
from sentinel_x.lab.models import FaultExperimentManifest, FaultMode
from sentinel_x.lab.orchestrator import (
    FaultExperimentRun,
    SystemdFaultExperimentOrchestrator,
)
from sentinel_x.systemd.observation import (
    SystemdServiceCollector,
    SystemdServiceEmission,
)

_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 0.02
_DEFAULT_STARTUP_TIMEOUT_SECONDS: Final[float] = 5.0
_DEFAULT_POST_RECOVERY_GRACE_SECONDS: Final[float] = 0.25
_DEFAULT_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
_DEFAULT_MAX_SAMPLES: Final[int] = 4096
_MAX_POLL_INTERVAL_SECONDS: Final[float] = 5.0
_MAX_GRACE_SECONDS: Final[float] = 5.0
_MAX_TIMEOUT_SECONDS: Final[float] = 30.0
_MAX_SAMPLES_BOUND: Final[int] = 65536


class DetectionEvaluationError(RuntimeError):
    """Base error for ground-truth-aligned detection evaluation."""


class DetectionEvaluationContractError(DetectionEvaluationError):
    """Raised when evaluation inputs or derived metrics are inconsistent."""


class DetectionEvaluationCaptureError(DetectionEvaluationError):
    """Raised when live detection sampling cannot be completed safely."""


class DetectionObservationCollector(Protocol):
    """Structural read-only service observation collector dependency."""

    def collect(self) -> SystemdServiceEmission:
        """Return an emission carrying one service observation event."""

        ...


class ServiceStateDetector(Protocol):
    """Structural detector dependency used by the live capture."""

    def assess(self, event: SentinelEvent) -> SystemdServiceHealthAssessment:
        """Assess one systemd observation event."""

        ...


class DetectionCapture(Protocol):
    """Structural bounded detection capture dependency."""

    def start(self) -> None:
        """Start capture and require a first successful sample."""

        ...

    def finish(
        self,
        *,
        post_recovery_grace_seconds: float,
    ) -> tuple[DetectionEvaluationSample, ...]:
        """Stop capture after a bounded post-recovery grace interval."""

        ...


class DetectionCaptureFactory(Protocol):
    """Factory for one evaluation capture bound to an exact target unit."""

    def __call__(self, unit_name: str) -> DetectionCapture:
        """Return one fresh capture for the supplied unit."""

        ...


class FaultExperimentRunner(Protocol):
    """Structural Phase 3 experiment runner dependency."""

    def run(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultExperimentRun:
        """Execute one complete frozen fault experiment."""

        ...


class MonotonicUsecClock(Protocol):
    """Structural monotonic microsecond clock dependency."""

    def __call__(self) -> int:
        """Return monotonic microseconds."""

        ...


class Sleeper(Protocol):
    """Structural bounded sleep dependency."""

    def __call__(self, seconds: float) -> None:
        """Sleep for the supplied duration."""

        ...


def _monotonic_usec() -> int:
    return time.monotonic_ns() // 1_000


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class DetectionEvaluationSample:
    """Compact immutable detector result observed during one live experiment."""

    assessed_monotonic_usec: int
    source_event_id: str
    assessment_id: str
    target_unit: str
    status: SystemdServiceHealthStatus
    anomaly_class: DetectionAnomalyClass | None
    load_state: str
    active_state: str
    sub_state: str
    main_pid: int | None
    state_change_monotonic_usec: int | None

    def __post_init__(self) -> None:
        _validate_nonnegative_int(
            self.assessed_monotonic_usec,
            field_name="assessed_monotonic_usec",
        )
        for field_name, value in (
            ("source_event_id", self.source_event_id),
            ("assessment_id", self.assessment_id),
            ("target_unit", self.target_unit),
            ("load_state", self.load_state),
            ("active_state", self.active_state),
            ("sub_state", self.sub_state),
        ):
            _validate_nonempty_text(value, field_name=field_name)
        if not isinstance(self.status, SystemdServiceHealthStatus):
            raise DetectionEvaluationContractError(
                "status must be a SystemdServiceHealthStatus"
            )
        if self.anomaly_class is not None and not isinstance(
            self.anomaly_class,
            DetectionAnomalyClass,
        ):
            raise DetectionEvaluationContractError(
                "anomaly_class must be a DetectionAnomalyClass or None"
            )
        if self.main_pid is not None:
            _validate_nonnegative_int(self.main_pid, field_name="main_pid")
        if self.state_change_monotonic_usec is not None:
            _validate_nonnegative_int(
                self.state_change_monotonic_usec,
                field_name="state_change_monotonic_usec",
            )
        if self.is_anomalous != (self.anomaly_class is not None):
            raise DetectionEvaluationContractError(
                "anomaly_class presence must match anomalous status"
            )
        self._validate_status_contract()

    def _validate_status_contract(self) -> None:
        if self.status is SystemdServiceHealthStatus.HEALTHY:
            if not (
                self.load_state == "loaded"
                and self.active_state == "active"
                and self.sub_state == "running"
                and self.main_pid is not None
                and self.main_pid > 0
            ):
                raise DetectionEvaluationContractError(
                    "healthy samples require loaded active/running state "
                    "with a live main PID"
                )
            return
        if self.status is SystemdServiceHealthStatus.INACTIVE:
            if (
                self.load_state != "loaded"
                or self.active_state != "inactive"
                or self.anomaly_class
                is not DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
            ):
                raise DetectionEvaluationContractError(
                    "inactive samples must preserve the inactive detector contract"
                )
            return
        if self.status is SystemdServiceHealthStatus.FAILED:
            if (
                self.load_state != "loaded"
                or self.active_state != "failed"
                or self.anomaly_class
                is not DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
            ):
                raise DetectionEvaluationContractError(
                    "failed samples must preserve the failed detector contract"
                )
            return
        if self.status is SystemdServiceHealthStatus.UNASSESSED:
            if self.anomaly_class is not None:
                raise DetectionEvaluationContractError(
                    "unassessed samples must not carry anomaly metadata"
                )
            return
        raise DetectionEvaluationContractError("unsupported detector status")

    @classmethod
    def from_assessment(
        cls,
        assessment: SystemdServiceHealthAssessment,
    ) -> DetectionEvaluationSample:
        """Project one frozen detector assessment into bounded evaluation data."""

        if not isinstance(assessment, SystemdServiceHealthAssessment):
            raise DetectionEvaluationContractError(
                "assessment must be a SystemdServiceHealthAssessment"
            )
        return cls(
            assessed_monotonic_usec=assessment.assessed_monotonic_usec,
            source_event_id=assessment.source_event_id,
            assessment_id=assessment.assessment_id,
            target_unit=assessment.target_unit,
            status=assessment.status,
            anomaly_class=assessment.anomaly_class,
            load_state=assessment.load_state,
            active_state=assessment.active_state,
            sub_state=assessment.sub_state,
            main_pid=assessment.main_pid,
            state_change_monotonic_usec=assessment.state_change_monotonic_usec,
        )

    @property
    def is_anomalous(self) -> bool:
        """Return whether this sample is one of the supported anomaly states."""

        return self.status in {
            SystemdServiceHealthStatus.INACTIVE,
            SystemdServiceHealthStatus.FAILED,
        }

    @property
    def is_healthy(self) -> bool:
        """Return whether this sample is a healthy detector result."""

        return self.status is SystemdServiceHealthStatus.HEALTHY

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly sample representation."""

        return {
            "assessed_monotonic_usec": self.assessed_monotonic_usec,
            "source_event_id": self.source_event_id,
            "assessment_id": self.assessment_id,
            "target_unit": self.target_unit,
            "status": self.status.value,
            "anomaly_class": (
                None if self.anomaly_class is None else self.anomaly_class.value
            ),
            "load_state": self.load_state,
            "active_state": self.active_state,
            "sub_state": self.sub_state,
            "main_pid": self.main_pid,
            "state_change_monotonic_usec": self.state_change_monotonic_usec,
            "is_anomalous": self.is_anomalous,
            "is_healthy": self.is_healthy,
        }


@dataclass(frozen=True, slots=True)
class FaultDetectionBenchmark:
    """Detector outcomes aligned to one frozen fault experiment ground truth."""

    experiment_id: str
    scenario_id: str
    target_unit: str
    fault_mode: FaultMode
    ground_truth_started_monotonic_usec: int
    ground_truth_ended_monotonic_usec: int
    evaluation_started_monotonic_usec: int
    recovery_confirmed_monotonic_usec: int
    detection_sample_count: int
    pre_fault_sample_count: int
    fault_window_sample_count: int
    post_recovery_sample_count: int
    fault_state_sample_count: int
    correct_fault_state_detection_count: int
    missed_fault_state_sample_count: int
    expected_detection_count: int
    fault_window_unassessed_count: int
    fault_window_other_anomaly_count: int
    pre_fault_false_positive_count: int
    post_recovery_false_positive_count: int
    first_expected_detection_monotonic_usec: int | None
    reported_fault_transition_monotonic_usec: int | None
    detection_transition_offset_usec: int | None
    detection_visibility_latency_usec: int | None
    ground_truth_confirmation_offset_usec: int | None
    first_recovery_healthy_monotonic_usec: int | None
    recovery_detection_latency_usec: int | None

    def __post_init__(self) -> None:
        for field_name, text_value in (
            ("experiment_id", self.experiment_id),
            ("scenario_id", self.scenario_id),
            ("target_unit", self.target_unit),
        ):
            _validate_nonempty_text(text_value, field_name=field_name)
        if not isinstance(self.fault_mode, FaultMode):
            raise DetectionEvaluationContractError("fault_mode must be a FaultMode")
        for field_name, integer_value in (
            (
                "ground_truth_started_monotonic_usec",
                self.ground_truth_started_monotonic_usec,
            ),
            (
                "ground_truth_ended_monotonic_usec",
                self.ground_truth_ended_monotonic_usec,
            ),
            (
                "evaluation_started_monotonic_usec",
                self.evaluation_started_monotonic_usec,
            ),
            (
                "recovery_confirmed_monotonic_usec",
                self.recovery_confirmed_monotonic_usec,
            ),
            ("detection_sample_count", self.detection_sample_count),
            ("pre_fault_sample_count", self.pre_fault_sample_count),
            ("fault_window_sample_count", self.fault_window_sample_count),
            ("post_recovery_sample_count", self.post_recovery_sample_count),
            ("fault_state_sample_count", self.fault_state_sample_count),
            (
                "correct_fault_state_detection_count",
                self.correct_fault_state_detection_count,
            ),
            ("missed_fault_state_sample_count", self.missed_fault_state_sample_count),
            ("expected_detection_count", self.expected_detection_count),
            ("fault_window_unassessed_count", self.fault_window_unassessed_count),
            (
                "fault_window_other_anomaly_count",
                self.fault_window_other_anomaly_count,
            ),
            (
                "pre_fault_false_positive_count",
                self.pre_fault_false_positive_count,
            ),
            (
                "post_recovery_false_positive_count",
                self.post_recovery_false_positive_count,
            ),
        ):
            _validate_nonnegative_int(integer_value, field_name=field_name)
        if (
            self.ground_truth_ended_monotonic_usec
            < self.ground_truth_started_monotonic_usec
        ):
            raise DetectionEvaluationContractError(
                "ground-truth end cannot precede ground-truth start"
            )
        if (
            self.ground_truth_started_monotonic_usec
            < self.evaluation_started_monotonic_usec
        ):
            raise DetectionEvaluationContractError(
                "ground truth cannot start before evaluation execution"
            )
        if (
            self.recovery_confirmed_monotonic_usec
            < self.ground_truth_ended_monotonic_usec
        ):
            raise DetectionEvaluationContractError(
                "recovery confirmation cannot precede ground-truth end"
            )
        if (
            self.recovery_confirmed_monotonic_usec
            < self.evaluation_started_monotonic_usec
        ):
            raise DetectionEvaluationContractError(
                "recovery confirmation cannot precede evaluation start"
            )
        if (
            self.pre_fault_sample_count
            + self.fault_window_sample_count
            + self.post_recovery_sample_count
            != self.detection_sample_count
        ):
            raise DetectionEvaluationContractError(
                "sample-window counts must equal detection_sample_count"
            )
        if self.fault_state_sample_count > self.fault_window_sample_count:
            raise DetectionEvaluationContractError(
                "fault_state_sample_count cannot exceed fault_window_sample_count"
            )
        if self.correct_fault_state_detection_count > self.fault_state_sample_count:
            raise DetectionEvaluationContractError(
                "correct fault-state detections cannot exceed fault-state samples"
            )
        if (
            self.correct_fault_state_detection_count
            + self.missed_fault_state_sample_count
            != self.fault_state_sample_count
        ):
            raise DetectionEvaluationContractError(
                "fault-state classification counts must partition fault-state samples"
            )
        if self.expected_detection_count > self.fault_window_sample_count:
            raise DetectionEvaluationContractError(
                "expected detections cannot exceed fault-window samples"
            )
        if self.expected_detection_count != self.correct_fault_state_detection_count:
            raise DetectionEvaluationContractError(
                "expected detections must equal correct fault-state detections"
            )
        if self.fault_window_unassessed_count > self.fault_window_sample_count:
            raise DetectionEvaluationContractError(
                "unassessed count cannot exceed fault-window samples"
            )
        if self.fault_window_other_anomaly_count > self.fault_window_sample_count:
            raise DetectionEvaluationContractError(
                "other anomaly count cannot exceed fault-window samples"
            )
        if self.pre_fault_false_positive_count > self.pre_fault_sample_count:
            raise DetectionEvaluationContractError(
                "pre-fault false positives cannot exceed pre-fault samples"
            )
        if self.post_recovery_false_positive_count > self.post_recovery_sample_count:
            raise DetectionEvaluationContractError(
                "post-recovery false positives cannot exceed post-recovery samples"
            )
        if self.first_expected_detection_monotonic_usec is None:
            if self.expected_detection_count != 0:
                raise DetectionEvaluationContractError(
                    "expected detections require a first detection timestamp"
                )
            if any(
                value is not None
                for value in (
                    self.detection_transition_offset_usec,
                    self.detection_visibility_latency_usec,
                    self.ground_truth_confirmation_offset_usec,
                )
            ):
                raise DetectionEvaluationContractError(
                    "detection timing metrics require an expected detection"
                )
        else:
            _validate_nonnegative_int(
                self.first_expected_detection_monotonic_usec,
                field_name="first_expected_detection_monotonic_usec",
            )
            if self.expected_detection_count == 0:
                raise DetectionEvaluationContractError(
                    "first detection timestamp requires an expected detection"
                )
            if not (
                self.evaluation_started_monotonic_usec
                <= self.first_expected_detection_monotonic_usec
                < self.recovery_confirmed_monotonic_usec
            ):
                raise DetectionEvaluationContractError(
                    "first detection must lie inside the evaluation fault window"
                )
        if self.reported_fault_transition_monotonic_usec is not None:
            _validate_nonnegative_int(
                self.reported_fault_transition_monotonic_usec,
                field_name="reported_fault_transition_monotonic_usec",
            )
        if self.detection_transition_offset_usec is not None:
            _validate_int(
                self.detection_transition_offset_usec,
                field_name="detection_transition_offset_usec",
            )
            if self.reported_fault_transition_monotonic_usec is None:
                raise DetectionEvaluationContractError(
                    "transition offset requires a reported transition"
                )
        if (
            self.first_expected_detection_monotonic_usec is not None
            and self.reported_fault_transition_monotonic_usec is not None
            and self.detection_transition_offset_usec is None
        ):
            raise DetectionEvaluationContractError(
                "a measurable transition and detection require a signed offset"
            )
        if (
            self.first_expected_detection_monotonic_usec is not None
            and self.reported_fault_transition_monotonic_usec is not None
            and self.detection_transition_offset_usec
            != self.first_expected_detection_monotonic_usec
            - self.reported_fault_transition_monotonic_usec
        ):
            raise DetectionEvaluationContractError(
                "transition offset must match detection minus reported transition"
            )
        if self.detection_visibility_latency_usec is not None:
            _validate_nonnegative_int(
                self.detection_visibility_latency_usec,
                field_name="detection_visibility_latency_usec",
            )
            if self.detection_transition_offset_usec is None or (
                self.detection_transition_offset_usec < 0
            ):
                raise DetectionEvaluationContractError(
                    "visibility latency requires a non-negative transition offset"
                )
        if self.ground_truth_confirmation_offset_usec is not None:
            _validate_int(
                self.ground_truth_confirmation_offset_usec,
                field_name="ground_truth_confirmation_offset_usec",
            )
        if (
            self.first_expected_detection_monotonic_usec is not None
            and self.ground_truth_confirmation_offset_usec is None
        ):
            raise DetectionEvaluationContractError(
                "an expected detection requires a ground-truth confirmation offset"
            )
        if (
            self.first_expected_detection_monotonic_usec is not None
            and self.ground_truth_confirmation_offset_usec
            != self.first_expected_detection_monotonic_usec
            - self.ground_truth_started_monotonic_usec
        ):
            raise DetectionEvaluationContractError(
                "ground-truth confirmation offset must match detection ordering"
            )
        if self.first_recovery_healthy_monotonic_usec is None:
            if self.recovery_detection_latency_usec is not None:
                raise DetectionEvaluationContractError(
                    "recovery latency requires a recovery healthy timestamp"
                )
        else:
            _validate_nonnegative_int(
                self.first_recovery_healthy_monotonic_usec,
                field_name="first_recovery_healthy_monotonic_usec",
            )
            if (
                self.first_recovery_healthy_monotonic_usec
                < self.recovery_confirmed_monotonic_usec
            ):
                raise DetectionEvaluationContractError(
                    "recovery healthy timestamp cannot precede confirmation"
                )
            expected_recovery_latency = (
                self.first_recovery_healthy_monotonic_usec
                - self.recovery_confirmed_monotonic_usec
            )
            if self.recovery_detection_latency_usec != expected_recovery_latency:
                raise DetectionEvaluationContractError(
                    "recovery latency must match the first post-recovery healthy sample"
                )
        if self.recovery_detection_latency_usec is not None:
            _validate_nonnegative_int(
                self.recovery_detection_latency_usec,
                field_name="recovery_detection_latency_usec",
            )

    @property
    def detection_coverage(self) -> bool:
        """Return whether the expected anomaly was observed at least once."""

        return self.expected_detection_count > 0

    @property
    def fault_state_classification_complete(self) -> bool:
        """Return whether every observed fault-state sample was classified correctly."""

        return (
            self.fault_state_sample_count > 0
            and self.missed_fault_state_sample_count == 0
        )

    @property
    def healthy_control_complete(self) -> bool:
        """Return whether both healthy-control windows contain observations."""

        return self.pre_fault_sample_count > 0 and self.post_recovery_sample_count > 0

    @property
    def healthy_control_clean(self) -> bool:
        """Return whether complete healthy controls contained no anomaly."""

        return (
            self.healthy_control_complete
            and self.pre_fault_false_positive_count == 0
            and self.post_recovery_false_positive_count == 0
        )

    def to_dict(self) -> dict[str, object]:
        """Return stable JSON-friendly evaluation metrics."""

        return {
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "target_unit": self.target_unit,
            "fault_mode": self.fault_mode.value,
            "ground_truth_started_monotonic_usec": (
                self.ground_truth_started_monotonic_usec
            ),
            "ground_truth_ended_monotonic_usec": (
                self.ground_truth_ended_monotonic_usec
            ),
            "evaluation_started_monotonic_usec": (
                self.evaluation_started_monotonic_usec
            ),
            "recovery_confirmed_monotonic_usec": (
                self.recovery_confirmed_monotonic_usec
            ),
            "detection_sample_count": self.detection_sample_count,
            "pre_fault_sample_count": self.pre_fault_sample_count,
            "fault_window_sample_count": self.fault_window_sample_count,
            "post_recovery_sample_count": self.post_recovery_sample_count,
            "fault_state_sample_count": self.fault_state_sample_count,
            "correct_fault_state_detection_count": (
                self.correct_fault_state_detection_count
            ),
            "missed_fault_state_sample_count": self.missed_fault_state_sample_count,
            "expected_detection_count": self.expected_detection_count,
            "fault_window_unassessed_count": self.fault_window_unassessed_count,
            "fault_window_other_anomaly_count": self.fault_window_other_anomaly_count,
            "pre_fault_false_positive_count": self.pre_fault_false_positive_count,
            "post_recovery_false_positive_count": (
                self.post_recovery_false_positive_count
            ),
            "first_expected_detection_monotonic_usec": (
                self.first_expected_detection_monotonic_usec
            ),
            "reported_fault_transition_monotonic_usec": (
                self.reported_fault_transition_monotonic_usec
            ),
            "detection_transition_offset_usec": self.detection_transition_offset_usec,
            "detection_visibility_latency_usec": self.detection_visibility_latency_usec,
            "ground_truth_confirmation_offset_usec": (
                self.ground_truth_confirmation_offset_usec
            ),
            "first_recovery_healthy_monotonic_usec": (
                self.first_recovery_healthy_monotonic_usec
            ),
            "recovery_detection_latency_usec": self.recovery_detection_latency_usec,
            "detection_coverage": self.detection_coverage,
            "fault_state_classification_complete": (
                self.fault_state_classification_complete
            ),
            "healthy_control_complete": self.healthy_control_complete,
            "healthy_control_clean": self.healthy_control_clean,
        }


@dataclass(frozen=True, slots=True)
class FaultDetectionEvaluationRun:
    """One Phase 3 experiment plus live detector samples and derived benchmark."""

    experiment_run: FaultExperimentRun
    detection_samples: tuple[DetectionEvaluationSample, ...]
    benchmark: FaultDetectionBenchmark

    def __post_init__(self) -> None:
        if not isinstance(self.experiment_run, FaultExperimentRun):
            raise DetectionEvaluationContractError(
                "experiment_run must be a FaultExperimentRun"
            )
        if not isinstance(self.detection_samples, tuple):
            raise DetectionEvaluationContractError("detection_samples must be a tuple")
        for sample in self.detection_samples:
            if not isinstance(sample, DetectionEvaluationSample):
                raise DetectionEvaluationContractError(
                    "detection_samples must contain typed samples"
                )
        if not isinstance(self.benchmark, FaultDetectionBenchmark):
            raise DetectionEvaluationContractError(
                "benchmark must be a FaultDetectionBenchmark"
            )
        if (
            self.benchmark.experiment_id
            != self.experiment_run.outcome.plan.experiment_id
        ):
            raise DetectionEvaluationContractError(
                "benchmark experiment identity must match experiment_run"
            )
        if self.benchmark.detection_sample_count != len(self.detection_samples):
            raise DetectionEvaluationContractError(
                "benchmark sample count must match detection_samples"
            )

    def to_dict(self) -> dict[str, object]:
        """Return a bounded JSON-friendly evaluation result."""

        return {
            "experiment": self.experiment_run.to_dict(),
            "detection_samples": [
                sample.to_dict() for sample in self.detection_samples
            ],
            "detection_benchmark": self.benchmark.to_dict(),
        }


class SystemdDetectionEvaluationCapture:
    """Continuously collect real systemd observations and frozen detector output."""

    def __init__(
        self,
        unit_name: str,
        *,
        collector: DetectionObservationCollector | None = None,
        detector: ServiceStateDetector | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        startup_timeout_seconds: float = _DEFAULT_STARTUP_TIMEOUT_SECONDS,
        join_timeout_seconds: float = _DEFAULT_JOIN_TIMEOUT_SECONDS,
        max_samples: int = _DEFAULT_MAX_SAMPLES,
        sleeper: Sleeper = _sleep,
    ) -> None:
        _validate_nonempty_text(unit_name, field_name="unit_name")
        self._unit_name = unit_name
        self._collector: DetectionObservationCollector = (
            SystemdServiceCollector("phase4b.live-detection", unit_name)
            if collector is None
            else collector
        )
        self._detector: ServiceStateDetector = (
            SystemdServiceStateDetector() if detector is None else detector
        )
        if not callable(getattr(self._collector, "collect", None)):
            raise TypeError("collector must expose a callable collect method")
        if not callable(getattr(self._detector, "assess", None)):
            raise TypeError("detector must expose a callable assess method")
        self._poll_interval_seconds = _validate_bounded_seconds(
            poll_interval_seconds,
            field_name="poll_interval_seconds",
            maximum=_MAX_POLL_INTERVAL_SECONDS,
        )
        self._startup_timeout_seconds = _validate_bounded_seconds(
            startup_timeout_seconds,
            field_name="startup_timeout_seconds",
            maximum=_MAX_TIMEOUT_SECONDS,
        )
        self._join_timeout_seconds = _validate_bounded_seconds(
            join_timeout_seconds,
            field_name="join_timeout_seconds",
            maximum=_MAX_TIMEOUT_SECONDS,
        )
        if isinstance(max_samples, bool) or not isinstance(max_samples, int):
            raise DetectionEvaluationContractError("max_samples must be an integer")
        if not 1 <= max_samples <= _MAX_SAMPLES_BOUND:
            raise DetectionEvaluationContractError(
                f"max_samples must be between 1 and {_MAX_SAMPLES_BOUND}"
            )
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._max_samples = max_samples
        self._sleeper = sleeper
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._first_sample_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples: list[DetectionEvaluationSample] = []
        self._failure: BaseException | None = None
        self._started = False
        self._finished = False

    def start(self) -> None:
        """Start bounded sampling and require one successful baseline sample."""

        with self._lock:
            if self._started:
                raise DetectionEvaluationCaptureError("capture cannot start twice")
            self._started = True
            self._thread = threading.Thread(
                target=self._run,
                name="sentinel-x-phase4b-detection",
                daemon=True,
            )
            self._thread.start()

        if not self._first_sample_event.wait(self._startup_timeout_seconds):
            self._stop_event.set()
            self._join_thread()
            if self._failure is not None:
                raise DetectionEvaluationCaptureError(
                    "detection capture failed before the first sample"
                ) from self._failure
            raise DetectionEvaluationCaptureError(
                "detection capture did not produce a baseline sample in time"
            )
        if self._failure is not None:
            self._stop_event.set()
            self._join_thread()
            raise DetectionEvaluationCaptureError(
                "detection capture failed before the first sample"
            ) from self._failure

    def finish(
        self,
        *,
        post_recovery_grace_seconds: float,
    ) -> tuple[DetectionEvaluationSample, ...]:
        """Stop capture after a bounded grace and return immutable samples."""

        grace = _validate_nonnegative_seconds(
            post_recovery_grace_seconds,
            field_name="post_recovery_grace_seconds",
            maximum=_MAX_GRACE_SECONDS,
        )
        with self._lock:
            if not self._started:
                raise DetectionEvaluationCaptureError(
                    "capture must start before finish"
                )
            if self._finished:
                raise DetectionEvaluationCaptureError("capture cannot finish twice")
            self._finished = True

        if grace > 0:
            self._sleeper(grace)
        self._stop_event.set()
        self._join_thread()
        if self._failure is not None:
            raise DetectionEvaluationCaptureError(
                "detection capture failed during sampling"
            ) from self._failure
        with self._lock:
            return tuple(self._samples)

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._sample_once()
                if len(self._samples) >= self._max_samples:
                    self._stop_event.set()
                    break
                if self._poll_interval_seconds > 0:
                    self._sleeper(self._poll_interval_seconds)
        except BaseException as exc:
            with self._lock:
                self._failure = exc
            self._stop_event.set()
        finally:
            self._first_sample_event.set()

    def _sample_once(self) -> None:
        emission = self._collector.collect()
        event = getattr(emission, "event", None)
        if not isinstance(event, SentinelEvent):
            raise DetectionEvaluationContractError(
                "collector emission must carry a SentinelEvent"
            )
        assessment = self._detector.assess(event)
        sample = DetectionEvaluationSample.from_assessment(assessment)
        if sample.target_unit != self._unit_name:
            raise DetectionEvaluationContractError(
                "detector assessment target does not match capture target"
            )
        with self._lock:
            if len(self._samples) < self._max_samples:
                self._samples.append(sample)
                self._first_sample_event.set()

    def _join_thread(self) -> None:
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=self._join_timeout_seconds)
        if thread.is_alive():
            raise DetectionEvaluationCaptureError(
                "detection capture thread did not stop within the join bound"
            )


class SystemdFaultDetectionEvaluator:
    """Wrap a frozen Phase 3 experiment with independent live detector capture."""

    def __init__(
        self,
        *,
        experiment_runner: FaultExperimentRunner | None = None,
        capture_factory: DetectionCaptureFactory | None = None,
        monotonic_usec_clock: MonotonicUsecClock = _monotonic_usec,
        pre_fault_grace_seconds: float = 0.25,
        post_recovery_grace_seconds: float = _DEFAULT_POST_RECOVERY_GRACE_SECONDS,
        sleeper: Sleeper = _sleep,
    ) -> None:
        self._experiment_runner: FaultExperimentRunner = (
            SystemdFaultExperimentOrchestrator()
            if experiment_runner is None
            else experiment_runner
        )
        if not callable(getattr(self._experiment_runner, "run", None)):
            raise TypeError("experiment_runner must expose a callable run method")
        if capture_factory is None:
            self._capture_factory: DetectionCaptureFactory = lambda unit_name: (
                SystemdDetectionEvaluationCapture(unit_name)
            )
        elif callable(capture_factory):
            self._capture_factory = capture_factory
        else:
            raise TypeError("capture_factory must be callable")
        if not callable(monotonic_usec_clock):
            raise TypeError("monotonic_usec_clock must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._clock = monotonic_usec_clock
        self._pre_fault_grace_seconds = _validate_nonnegative_seconds(
            pre_fault_grace_seconds,
            field_name="pre_fault_grace_seconds",
            maximum=_MAX_GRACE_SECONDS,
        )
        self._post_recovery_grace_seconds = _validate_nonnegative_seconds(
            post_recovery_grace_seconds,
            field_name="post_recovery_grace_seconds",
            maximum=_MAX_GRACE_SECONDS,
        )
        self._sleeper = sleeper

    def run(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultDetectionEvaluationRun:
        """Execute one live detection evaluation without direct mutation here."""

        if not isinstance(manifest, FaultExperimentManifest):
            raise DetectionEvaluationContractError(
                "manifest must be a FaultExperimentManifest"
            )
        if not isinstance(artifact, SystemdLabFixtureArtifact):
            raise DetectionEvaluationContractError(
                "artifact must be a SystemdLabFixtureArtifact"
            )
        target_unit = manifest.scenario.target_unit
        if artifact.unit_name != target_unit:
            raise DetectionEvaluationContractError(
                "manifest target must match the lab fixture artifact"
            )
        capture = self._capture_factory(target_unit)
        if not callable(getattr(capture, "start", None)) or not callable(
            getattr(capture, "finish", None)
        ):
            raise DetectionEvaluationContractError(
                "capture_factory must return a detection capture contract"
            )

        capture.start()
        try:
            if self._pre_fault_grace_seconds > 0:
                self._sleeper(self._pre_fault_grace_seconds)
            evaluation_started = _validated_clock_value(self._clock())
            experiment_run = self._experiment_runner.run(manifest, artifact)
            recovery_confirmed = _validated_clock_value(self._clock())
        except BaseException:
            try:
                capture.finish(post_recovery_grace_seconds=0.0)
            except BaseException:
                pass
            raise

        samples = capture.finish(
            post_recovery_grace_seconds=self._post_recovery_grace_seconds
        )
        benchmark = build_fault_detection_benchmark(
            experiment_run,
            samples,
            evaluation_started_monotonic_usec=evaluation_started,
            recovery_confirmed_monotonic_usec=recovery_confirmed,
        )
        return FaultDetectionEvaluationRun(
            experiment_run=experiment_run,
            detection_samples=samples,
            benchmark=benchmark,
        )


def build_fault_detection_benchmark(
    experiment_run: FaultExperimentRun,
    samples: tuple[DetectionEvaluationSample, ...],
    *,
    evaluation_started_monotonic_usec: int,
    recovery_confirmed_monotonic_usec: int,
) -> FaultDetectionBenchmark:
    """Derive conservative detection metrics without fabricating missing timing."""

    if not isinstance(experiment_run, FaultExperimentRun):
        raise DetectionEvaluationContractError(
            "experiment_run must be a FaultExperimentRun"
        )
    if not isinstance(samples, tuple):
        raise DetectionEvaluationContractError("samples must be a tuple")
    if not samples:
        raise DetectionEvaluationContractError(
            "detection evaluation requires at least one sample"
        )
    for sample in samples:
        if not isinstance(sample, DetectionEvaluationSample):
            raise DetectionEvaluationContractError(
                "samples must contain DetectionEvaluationSample values"
            )
    evaluation_started = _validated_clock_value(evaluation_started_monotonic_usec)
    recovery_confirmed = _validated_clock_value(recovery_confirmed_monotonic_usec)
    if recovery_confirmed < evaluation_started:
        raise DetectionEvaluationContractError(
            "recovery confirmation cannot precede evaluation start"
        )

    outcome = experiment_run.outcome
    plan = outcome.plan
    target_unit = plan.target_unit
    for sample in samples:
        if sample.target_unit != target_unit:
            raise DetectionEvaluationContractError(
                "all detection samples must match the experiment target"
            )
    ordered = tuple(sorted(samples, key=lambda sample: sample.assessed_monotonic_usec))
    if ordered != samples:
        raise DetectionEvaluationContractError(
            "detection samples must be monotonic by assessment time"
        )

    expected_status, expected_class, expected_active_state = _expected_fault_mapping(
        plan.fault_mode
    )
    pre_fault = tuple(
        sample
        for sample in samples
        if sample.assessed_monotonic_usec < evaluation_started
    )
    fault_window = tuple(
        sample
        for sample in samples
        if evaluation_started <= sample.assessed_monotonic_usec < recovery_confirmed
    )
    post_recovery = tuple(
        sample
        for sample in samples
        if sample.assessed_monotonic_usec >= recovery_confirmed
    )

    fault_state_samples = tuple(
        sample
        for sample in fault_window
        if sample.active_state == expected_active_state
    )
    correct_fault_state_samples = tuple(
        sample
        for sample in fault_state_samples
        if sample.status is expected_status and sample.anomaly_class is expected_class
    )
    expected_detections = tuple(
        sample
        for sample in fault_window
        if sample.status is expected_status and sample.anomaly_class is expected_class
    )
    other_anomalies = tuple(
        sample
        for sample in fault_window
        if sample.is_anomalous and sample.anomaly_class is not expected_class
    )
    unassessed = tuple(
        sample
        for sample in fault_window
        if sample.status is SystemdServiceHealthStatus.UNASSESSED
    )
    pre_false_positives = tuple(sample for sample in pre_fault if sample.is_anomalous)
    post_false_positives = tuple(
        sample for sample in post_recovery if sample.is_anomalous
    )

    first_detection = (
        None
        if not expected_detections
        else expected_detections[0].assessed_monotonic_usec
    )
    transition = experiment_run.benchmark.reported_fault_transition_monotonic_usec
    transition_offset: int | None = None
    visibility_latency: int | None = None
    confirmation_offset: int | None = None
    if first_detection is not None:
        if transition is not None:
            transition_offset = first_detection - transition
            if transition_offset >= 0:
                visibility_latency = transition_offset
        confirmation_offset = (
            first_detection - outcome.ground_truth.started_monotonic_usec
        )

    first_recovery_healthy: int | None = None
    recovery_detection_latency: int | None = None
    for sample in post_recovery:
        if sample.is_healthy:
            first_recovery_healthy = sample.assessed_monotonic_usec
            recovery_detection_latency = first_recovery_healthy - recovery_confirmed
            break

    ground_truth_end = outcome.ground_truth.ended_monotonic_usec
    if ground_truth_end is None:
        raise DetectionEvaluationContractError(
            "detection evaluation requires closed ground truth"
        )

    return FaultDetectionBenchmark(
        experiment_id=plan.experiment_id,
        scenario_id=plan.scenario_id,
        target_unit=target_unit,
        fault_mode=plan.fault_mode,
        ground_truth_started_monotonic_usec=(
            outcome.ground_truth.started_monotonic_usec
        ),
        ground_truth_ended_monotonic_usec=ground_truth_end,
        evaluation_started_monotonic_usec=evaluation_started,
        recovery_confirmed_monotonic_usec=recovery_confirmed,
        detection_sample_count=len(samples),
        pre_fault_sample_count=len(pre_fault),
        fault_window_sample_count=len(fault_window),
        post_recovery_sample_count=len(post_recovery),
        fault_state_sample_count=len(fault_state_samples),
        correct_fault_state_detection_count=len(correct_fault_state_samples),
        missed_fault_state_sample_count=(
            len(fault_state_samples) - len(correct_fault_state_samples)
        ),
        expected_detection_count=len(expected_detections),
        fault_window_unassessed_count=len(unassessed),
        fault_window_other_anomaly_count=len(other_anomalies),
        pre_fault_false_positive_count=len(pre_false_positives),
        post_recovery_false_positive_count=len(post_false_positives),
        first_expected_detection_monotonic_usec=first_detection,
        reported_fault_transition_monotonic_usec=transition,
        detection_transition_offset_usec=transition_offset,
        detection_visibility_latency_usec=visibility_latency,
        ground_truth_confirmation_offset_usec=confirmation_offset,
        first_recovery_healthy_monotonic_usec=first_recovery_healthy,
        recovery_detection_latency_usec=recovery_detection_latency,
    )


def _expected_fault_mapping(
    mode: FaultMode,
) -> tuple[
    SystemdServiceHealthStatus,
    DetectionAnomalyClass,
    str,
]:
    if mode is FaultMode.SERVICE_INACTIVE:
        return (
            SystemdServiceHealthStatus.INACTIVE,
            DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE,
            "inactive",
        )
    if mode is FaultMode.SERVICE_FAILED:
        return (
            SystemdServiceHealthStatus.FAILED,
            DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
            "failed",
        )
    raise DetectionEvaluationContractError("unsupported fault mode")


def _validated_clock_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DetectionEvaluationContractError(
            "monotonic clock must return a non-negative integer"
        )
    return value


def _validate_nonnegative_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DetectionEvaluationContractError(
            f"{field_name} must be a non-negative integer"
        )


def _validate_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DetectionEvaluationContractError(f"{field_name} must be an integer")


def _validate_nonempty_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise DetectionEvaluationContractError(
            f"{field_name} must be non-empty text without NUL bytes"
        )


def _validate_bounded_seconds(
    value: object,
    *,
    field_name: str,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DetectionEvaluationContractError(f"{field_name} must be numeric")
    normalized = float(value)
    if not 0 < normalized <= maximum:
        raise DetectionEvaluationContractError(
            f"{field_name} must be > 0 and <= {maximum}"
        )
    return normalized


def _validate_nonnegative_seconds(
    value: object,
    *,
    field_name: str,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DetectionEvaluationContractError(f"{field_name} must be numeric")
    normalized = float(value)
    if not 0 <= normalized <= maximum:
        raise DetectionEvaluationContractError(
            f"{field_name} must be >= 0 and <= {maximum}"
        )
    return normalized
