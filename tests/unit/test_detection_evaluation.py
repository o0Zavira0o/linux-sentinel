"""Tests for ground-truth-aligned live detector evaluation."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.detection.evaluation import (
    DetectionEvaluationCaptureError,
    DetectionEvaluationContractError,
    DetectionEvaluationSample,
    FaultDetectionEvaluationRun,
    SystemdDetectionEvaluationCapture,
    SystemdFaultDetectionEvaluator,
    build_fault_detection_benchmark,
)
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
    DetectionBasis,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)
from sentinel_x.lab import (
    FaultExperimentBenchmark,
    FaultExperimentManifest,
    FaultExperimentRun,
    FaultExperimentTelemetrySample,
    FaultGroundTruthWindow,
    FaultInjectionOutcome,
    FaultMode,
    FaultScenario,
    LabFaultOperation,
    SystemdLabFaultPlan,
    SystemdLabFixtureSpec,
    SystemdLabServiceState,
    build_systemd_lab_fixture,
)
from sentinel_x.systemd.observation import SystemdServiceEmission

_UNIT = "sentinel-x-lab-detection-01.service"
_EXPERIMENT_ID = "exp-0123456789abcdef0123456789abcdef"
_NOW = datetime(2026, 8, 14, 18, 0, tzinfo=timezone.utc)


def _artifact():
    return build_systemd_lab_fixture(
        SystemdLabFixtureSpec("detection-01", runtime_max_seconds=120)
    )


def _manifest(mode: FaultMode = FaultMode.SERVICE_FAILED) -> FaultExperimentManifest:
    return FaultExperimentManifest(
        FaultScenario(
            scenario_id="phase4b-evaluation",
            description="Ground-truth-aligned detector evaluation test.",
            target_unit=_UNIT,
            fault_mode=mode,
            evidence_grace_seconds=1.0,
        ),
        experiment_id=_EXPERIMENT_ID,
        created_at=_NOW,
    )


def _state(
    active: str,
    sub: str,
    pid: int | None,
    result: str,
) -> SystemdLabServiceState:
    return SystemdLabServiceState(
        unit_name=_UNIT,
        load_state="loaded",
        active_state=active,
        sub_state=sub,
        main_pid=pid,
        result=result,
    )


def _outcome(mode: FaultMode = FaultMode.SERVICE_FAILED) -> FaultInjectionOutcome:
    manifest = _manifest(mode)
    truth = FaultGroundTruthWindow.from_manifest(
        manifest,
        started_at=_NOW,
        started_monotonic_usec=1_500,
    ).close(
        ended_at=datetime(2026, 8, 14, 18, 0, 1, tzinfo=timezone.utc),
        ended_monotonic_usec=2_800,
    )
    expected = "inactive" if mode is FaultMode.SERVICE_INACTIVE else "failed"
    operation = (
        LabFaultOperation.STOP_SERVICE
        if mode is FaultMode.SERVICE_INACTIVE
        else LabFaultOperation.ABORT_MAIN_PROCESS
    )
    return FaultInjectionOutcome(
        plan=SystemdLabFaultPlan(
            experiment_id=manifest.experiment_id,
            scenario_id=manifest.scenario.scenario_id,
            target_unit=_UNIT,
            fault_mode=mode,
            operation=operation,
            expected_fault_active_states=(expected,),
            recovery_operations=("start", "reset-failed"),
            artifact_sha256=_artifact().sha256,
        ),
        ground_truth=truth,
        baseline_state=_state("active", "running", 10, "success"),
        fault_state=_state(
            expected,
            "dead" if expected == "inactive" else "failed",
            0,
            "success" if expected == "inactive" else "signal",
        ),
        recovered_state=_state("active", "running", 20, "success"),
    )


def _lab_sample(observed: int) -> FaultExperimentTelemetrySample:
    return FaultExperimentTelemetrySample(
        observed_monotonic_usec=observed,
        service_event_id=f"service-{observed}",
        service_active_state="failed",
        service_sub_state="failed",
        service_main_pid=None,
        service_state_change_monotonic_usec=1_080,
        journal_event_id=None,
        journal_entry_monotonic_usec=(),
        correlated_entry_monotonic_usec=(),
        exact_invocation_entry_monotonic_usec=(),
    )


def _experiment_run(
    mode: FaultMode = FaultMode.SERVICE_FAILED,
    *,
    transition: int | None = 1_080,
) -> FaultExperimentRun:
    outcome = _outcome(mode)
    telemetry = (_lab_sample(1_100),)
    return FaultExperimentRun(
        outcome=outcome,
        benchmark=FaultExperimentBenchmark(
            experiment_id=outcome.plan.experiment_id,
            scenario_id=outcome.plan.scenario_id,
            target_unit=_UNIT,
            fault_mode=mode.value,
            ground_truth_duration_usec=1_300,
            telemetry_sample_count=1,
            fault_service_sample_count=1,
            fault_journal_entry_count=0,
            fault_correlated_entry_count=0,
            fault_exact_invocation_entry_count=0,
            reported_fault_transition_monotonic_usec=transition,
            ground_truth_confirmation_lag_usec=(
                None if transition is None else 1_500 - transition
            ),
            service_fault_visibility_latency_usec=(None if transition is None else 20),
            journal_fault_evidence_latency_usec=None,
            correlated_fault_evidence_latency_usec=None,
            exact_invocation_evidence_latency_usec=None,
            recovery_visibility_latency_usec=100,
        ),
        telemetry_samples=telemetry,
    )


def _sample(
    assessed: int,
    status: SystemdServiceHealthStatus,
    *,
    active: str | None = None,
    target: str = _UNIT,
) -> DetectionEvaluationSample:
    if status is SystemdServiceHealthStatus.HEALTHY:
        anomaly = None
        resolved_active = "active" if active is None else active
        sub = "running"
        pid = 10
    elif status is SystemdServiceHealthStatus.INACTIVE:
        anomaly = DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
        resolved_active = "inactive" if active is None else active
        sub = "dead"
        pid = None
    elif status is SystemdServiceHealthStatus.FAILED:
        anomaly = DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
        resolved_active = "failed" if active is None else active
        sub = "failed"
        pid = None
    else:
        anomaly = None
        resolved_active = "activating" if active is None else active
        sub = "start"
        pid = None
    return DetectionEvaluationSample(
        assessed_monotonic_usec=assessed,
        source_event_id=f"event-{assessed}",
        assessment_id=f"asmt-{assessed}",
        target_unit=target,
        status=status,
        anomaly_class=anomaly,
        load_state="loaded",
        active_state=resolved_active,
        sub_state=sub,
        main_pid=pid,
        state_change_monotonic_usec=None,
    )


def _assessment(
    assessed: int,
    status: SystemdServiceHealthStatus,
    *,
    target: str = _UNIT,
) -> SystemdServiceHealthAssessment:
    if status is SystemdServiceHealthStatus.HEALTHY:
        anomaly = None
        severity = None
        active = "active"
        sub = "running"
        pid = 10
    elif status is SystemdServiceHealthStatus.FAILED:
        anomaly = DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
        severity = EventSeverity.ERROR
        active = "failed"
        sub = "failed"
        pid = None
    else:
        anomaly = None
        severity = None
        active = "activating"
        sub = "start"
        pid = None
    return SystemdServiceHealthAssessment(
        assessment_id="asmt-" + "a" * 64,
        source_event_id=f"source-{assessed}",
        target_unit=target,
        canonical_unit=target,
        source_observed_at=_NOW,
        assessed_at=_NOW,
        assessed_monotonic_usec=assessed,
        state_change_monotonic_usec=None,
        load_state="loaded",
        active_state=active,
        sub_state=sub,
        main_pid=pid,
        result=None,
        status=status,
        anomaly_class=anomaly,
        severity=severity,
        basis=DetectionBasis.DIRECT_SYSTEMD_STATE,
    )


def _event(index: int = 1) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.systemd.service",
        message="service",
        event_id=f"event-{index}",
        attributes={"index": index},
        occurred_at=_NOW,
    )


class _Collector:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls = 0
        self.error = error

    def collect(self) -> SystemdServiceEmission:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SystemdServiceEmission("phase4b.test", _event(self.calls))


class _Detector:
    def __init__(
        self,
        statuses: tuple[SystemdServiceHealthStatus, ...],
        *,
        target: str = _UNIT,
    ) -> None:
        self.statuses = statuses
        self.target = target
        self.calls = 0

    def assess(self, event: SentinelEvent) -> SystemdServiceHealthAssessment:
        del event
        index = min(self.calls, len(self.statuses) - 1)
        self.calls += 1
        return _assessment(1_000 + self.calls, self.statuses[index], target=self.target)


class _Capture:
    def __init__(self, samples, *, start_error=None, finish_error=None) -> None:
        self.samples = tuple(samples)
        self.start_error = start_error
        self.finish_error = finish_error
        self.started = 0
        self.finished = 0
        self.grace_values: list[float] = []

    def start(self) -> None:
        self.started += 1
        if self.start_error is not None:
            raise self.start_error

    def finish(self, *, post_recovery_grace_seconds: float):
        self.finished += 1
        self.grace_values.append(post_recovery_grace_seconds)
        if self.finish_error is not None:
            raise self.finish_error
        return self.samples


class _Runner:
    def __init__(self, result: FaultExperimentRun | BaseException) -> None:
        self.result = result
        self.calls = 0

    def run(self, manifest, artifact):
        del manifest, artifact
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _Clock:
    def __init__(self, values) -> None:
        self.values = list(values)

    def __call__(self) -> int:
        if not self.values:
            raise RuntimeError("clock exhausted")
        return self.values.pop(0)


class DetectionEvaluationTests(unittest.TestCase):
    """Validate evaluation contracts, metrics, capture, and orchestration."""

    def test_sample_projects_typed_assessment_and_serializes(self) -> None:
        sample = DetectionEvaluationSample.from_assessment(
            _assessment(1_234, SystemdServiceHealthStatus.FAILED)
        )

        self.assertTrue(sample.is_anomalous)
        self.assertFalse(sample.is_healthy)
        self.assertEqual(sample.to_dict()["status"], "failed")
        json.dumps(sample.to_dict())

    def test_sample_rejects_untyped_assessment(self) -> None:
        with self.assertRaises(DetectionEvaluationContractError):
            DetectionEvaluationSample.from_assessment(object())

    def test_sample_rejects_anomaly_metadata_status_mismatch(self) -> None:
        with self.assertRaises(DetectionEvaluationContractError):
            DetectionEvaluationSample(
                assessed_monotonic_usec=1,
                source_event_id="event",
                assessment_id="assessment",
                target_unit=_UNIT,
                status=SystemdServiceHealthStatus.HEALTHY,
                anomaly_class=DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
                load_state="loaded",
                active_state="active",
                sub_state="running",
                main_pid=1,
                state_change_monotonic_usec=None,
            )

    def test_sample_preserves_frozen_status_state_contract(self) -> None:
        with self.assertRaises(DetectionEvaluationContractError):
            DetectionEvaluationSample(
                assessed_monotonic_usec=1,
                source_event_id="event",
                assessment_id="assessment",
                target_unit=_UNIT,
                status=SystemdServiceHealthStatus.FAILED,
                anomaly_class=DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED,
                load_state="loaded",
                active_state="active",
                sub_state="running",
                main_pid=1,
                state_change_monotonic_usec=None,
            )

    def test_benchmark_derives_failed_detection_and_signed_offsets(self) -> None:
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_050, SystemdServiceHealthStatus.UNASSESSED),
            _sample(1_100, SystemdServiceHealthStatus.FAILED),
            _sample(1_200, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )

        benchmark = build_fault_detection_benchmark(
            _experiment_run(),
            samples,
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )

        self.assertTrue(benchmark.detection_coverage)
        self.assertTrue(benchmark.fault_state_classification_complete)
        self.assertTrue(benchmark.healthy_control_clean)
        self.assertEqual(benchmark.detection_visibility_latency_usec, 20)
        self.assertEqual(benchmark.detection_transition_offset_usec, 20)
        self.assertEqual(benchmark.ground_truth_confirmation_offset_usec, -400)
        self.assertEqual(benchmark.recovery_detection_latency_usec, 100)
        self.assertEqual(benchmark.fault_window_unassessed_count, 1)

    def test_benchmark_keeps_missing_inactive_transition_explicit(self) -> None:
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_100, SystemdServiceHealthStatus.INACTIVE),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )

        benchmark = build_fault_detection_benchmark(
            _experiment_run(FaultMode.SERVICE_INACTIVE, transition=None),
            samples,
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )

        self.assertTrue(benchmark.detection_coverage)
        self.assertIsNone(benchmark.reported_fault_transition_monotonic_usec)
        self.assertIsNone(benchmark.detection_transition_offset_usec)
        self.assertIsNone(benchmark.detection_visibility_latency_usec)
        self.assertEqual(benchmark.ground_truth_confirmation_offset_usec, -400)

    def test_benchmark_preserves_pretransition_detection_without_fake_latency(
        self,
    ) -> None:
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_050, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )

        benchmark = build_fault_detection_benchmark(
            _experiment_run(transition=1_080),
            samples,
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )

        self.assertEqual(benchmark.detection_transition_offset_usec, -30)
        self.assertIsNone(benchmark.detection_visibility_latency_usec)

    def test_benchmark_records_run_level_false_negative(self) -> None:
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(
                1_100,
                SystemdServiceHealthStatus.UNASSESSED,
                active="failed",
            ),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )

        benchmark = build_fault_detection_benchmark(
            _experiment_run(),
            samples,
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )

        self.assertFalse(benchmark.detection_coverage)
        self.assertEqual(benchmark.fault_state_sample_count, 1)
        self.assertEqual(benchmark.missed_fault_state_sample_count, 1)
        self.assertFalse(benchmark.fault_state_classification_complete)
        self.assertIsNone(benchmark.first_expected_detection_monotonic_usec)

    def test_benchmark_counts_healthy_control_false_positives(self) -> None:
        samples = (
            _sample(900, SystemdServiceHealthStatus.FAILED),
            _sample(1_100, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.INACTIVE),
        )

        benchmark = build_fault_detection_benchmark(
            _experiment_run(),
            samples,
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )

        self.assertEqual(benchmark.pre_fault_false_positive_count, 1)
        self.assertEqual(benchmark.post_recovery_false_positive_count, 1)
        self.assertFalse(benchmark.healthy_control_clean)

    def test_benchmark_does_not_claim_clean_controls_when_window_is_missing(
        self,
    ) -> None:
        benchmark = build_fault_detection_benchmark(
            _experiment_run(),
            (
                _sample(900, SystemdServiceHealthStatus.HEALTHY),
                _sample(1_100, SystemdServiceHealthStatus.FAILED),
            ),
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )

        self.assertFalse(benchmark.healthy_control_complete)
        self.assertFalse(benchmark.healthy_control_clean)
        self.assertIsNone(benchmark.recovery_detection_latency_usec)

    def test_benchmark_counts_other_fault_window_anomaly_separately(self) -> None:
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_050, SystemdServiceHealthStatus.INACTIVE),
            _sample(1_100, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )

        benchmark = build_fault_detection_benchmark(
            _experiment_run(),
            samples,
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )

        self.assertEqual(benchmark.fault_window_other_anomaly_count, 1)
        self.assertTrue(benchmark.detection_coverage)

    def test_benchmark_requires_monotonic_sample_order(self) -> None:
        with self.assertRaises(DetectionEvaluationContractError):
            build_fault_detection_benchmark(
                _experiment_run(),
                (
                    _sample(1_100, SystemdServiceHealthStatus.FAILED),
                    _sample(900, SystemdServiceHealthStatus.HEALTHY),
                ),
                evaluation_started_monotonic_usec=1_000,
                recovery_confirmed_monotonic_usec=3_000,
            )

    def test_benchmark_rejects_mismatched_target(self) -> None:
        with self.assertRaises(DetectionEvaluationContractError):
            build_fault_detection_benchmark(
                _experiment_run(),
                (
                    _sample(
                        1_100,
                        SystemdServiceHealthStatus.FAILED,
                        target="other.service",
                    ),
                ),
                evaluation_started_monotonic_usec=1_000,
                recovery_confirmed_monotonic_usec=3_000,
            )

    def test_benchmark_requires_nonempty_typed_samples(self) -> None:
        with self.assertRaises(DetectionEvaluationContractError):
            build_fault_detection_benchmark(
                _experiment_run(),
                (),
                evaluation_started_monotonic_usec=1_000,
                recovery_confirmed_monotonic_usec=3_000,
            )
        with self.assertRaises(DetectionEvaluationContractError):
            build_fault_detection_benchmark(
                _experiment_run(),
                (object(),),
                evaluation_started_monotonic_usec=1_000,
                recovery_confirmed_monotonic_usec=3_000,
            )

    def test_benchmark_rejects_regressing_boundaries(self) -> None:
        with self.assertRaises(DetectionEvaluationContractError):
            build_fault_detection_benchmark(
                _experiment_run(),
                (_sample(1_100, SystemdServiceHealthStatus.FAILED),),
                evaluation_started_monotonic_usec=3_000,
                recovery_confirmed_monotonic_usec=1_000,
            )

    def test_benchmark_model_rejects_impossible_sample_partition(self) -> None:
        benchmark = build_fault_detection_benchmark(
            _experiment_run(),
            (
                _sample(900, SystemdServiceHealthStatus.HEALTHY),
                _sample(1_100, SystemdServiceHealthStatus.FAILED),
                _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
            ),
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )
        values = benchmark.to_dict()
        self.assertTrue(values["detection_coverage"])
        with self.assertRaises(DetectionEvaluationContractError):
            replace(benchmark, detection_sample_count=99)

    def test_benchmark_model_requires_signed_offsets_for_detection(self) -> None:
        benchmark = build_fault_detection_benchmark(
            _experiment_run(),
            (
                _sample(900, SystemdServiceHealthStatus.HEALTHY),
                _sample(1_100, SystemdServiceHealthStatus.FAILED),
                _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
            ),
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )

        with self.assertRaises(DetectionEvaluationContractError):
            replace(benchmark, detection_transition_offset_usec=None)
        with self.assertRaises(DetectionEvaluationContractError):
            replace(benchmark, ground_truth_confirmation_offset_usec=None)

    def test_evaluation_run_validates_identity_and_sample_count(self) -> None:
        experiment = _experiment_run()
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_100, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )
        benchmark = build_fault_detection_benchmark(
            experiment,
            samples,
            evaluation_started_monotonic_usec=1_000,
            recovery_confirmed_monotonic_usec=3_000,
        )
        run = FaultDetectionEvaluationRun(experiment, samples, benchmark)

        self.assertEqual(run.benchmark.experiment_id, _EXPERIMENT_ID)
        json.dumps(run.to_dict())

    def test_capture_collects_bounded_typed_samples(self) -> None:
        collector = _Collector()
        detector = _Detector((SystemdServiceHealthStatus.HEALTHY,))
        capture = SystemdDetectionEvaluationCapture(
            _UNIT,
            collector=collector,
            detector=detector,
            poll_interval_seconds=0.001,
            startup_timeout_seconds=1.0,
            join_timeout_seconds=1.0,
            max_samples=3,
            sleeper=lambda _seconds: None,
        )

        capture.start()
        samples = capture.finish(post_recovery_grace_seconds=0.0)

        self.assertGreaterEqual(len(samples), 1)
        self.assertLessEqual(len(samples), 3)
        self.assertTrue(all(sample.target_unit == _UNIT for sample in samples))

    def test_capture_reports_failure_before_first_sample(self) -> None:
        capture = SystemdDetectionEvaluationCapture(
            _UNIT,
            collector=_Collector(error=RuntimeError("boom")),
            detector=_Detector((SystemdServiceHealthStatus.HEALTHY,)),
            poll_interval_seconds=0.001,
            startup_timeout_seconds=1.0,
            join_timeout_seconds=1.0,
            sleeper=lambda _seconds: None,
        )

        with self.assertRaises(DetectionEvaluationCaptureError):
            capture.start()

    def test_capture_rejects_target_drift(self) -> None:
        capture = SystemdDetectionEvaluationCapture(
            _UNIT,
            collector=_Collector(),
            detector=_Detector(
                (SystemdServiceHealthStatus.HEALTHY,),
                target="other.service",
            ),
            poll_interval_seconds=0.001,
            startup_timeout_seconds=1.0,
            join_timeout_seconds=1.0,
            sleeper=lambda _seconds: None,
        )

        with self.assertRaises(DetectionEvaluationCaptureError):
            capture.start()

    def test_capture_cannot_start_or_finish_twice(self) -> None:
        capture = SystemdDetectionEvaluationCapture(
            _UNIT,
            collector=_Collector(),
            detector=_Detector((SystemdServiceHealthStatus.HEALTHY,)),
            poll_interval_seconds=0.001,
            startup_timeout_seconds=1.0,
            join_timeout_seconds=1.0,
            max_samples=2,
            sleeper=lambda _seconds: None,
        )

        capture.start()
        with self.assertRaises(DetectionEvaluationCaptureError):
            capture.start()
        capture.finish(post_recovery_grace_seconds=0.0)
        with self.assertRaises(DetectionEvaluationCaptureError):
            capture.finish(post_recovery_grace_seconds=0.0)

    def test_capture_finish_requires_start(self) -> None:
        capture = SystemdDetectionEvaluationCapture(
            _UNIT,
            collector=_Collector(),
            detector=_Detector((SystemdServiceHealthStatus.HEALTHY,)),
        )

        with self.assertRaises(DetectionEvaluationCaptureError):
            capture.finish(post_recovery_grace_seconds=0.0)

    def test_capture_constructor_rejects_invalid_bounds(self) -> None:
        for kwargs in (
            {"poll_interval_seconds": 0},
            {"startup_timeout_seconds": 0},
            {"join_timeout_seconds": 0},
            {"max_samples": 0},
            {"max_samples": True},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DetectionEvaluationContractError):
                    SystemdDetectionEvaluationCapture(_UNIT, **kwargs)

    def test_evaluator_wraps_experiment_with_capture_boundaries(self) -> None:
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_100, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )
        capture = _Capture(samples)
        runner = _Runner(_experiment_run())
        sleeps: list[float] = []
        evaluator = SystemdFaultDetectionEvaluator(
            experiment_runner=runner,
            capture_factory=lambda _unit: capture,
            monotonic_usec_clock=_Clock((1_000, 3_000)),
            pre_fault_grace_seconds=0.1,
            post_recovery_grace_seconds=0.2,
            sleeper=sleeps.append,
        )

        result = evaluator.run(_manifest(), _artifact())

        self.assertEqual(capture.started, 1)
        self.assertEqual(capture.finished, 1)
        self.assertEqual(capture.grace_values, [0.2])
        self.assertEqual(sleeps, [0.1])
        self.assertEqual(runner.calls, 1)
        self.assertTrue(result.benchmark.detection_coverage)

    def test_evaluator_stops_capture_without_grace_when_experiment_fails(self) -> None:
        capture = _Capture((_sample(900, SystemdServiceHealthStatus.HEALTHY),))
        runner = _Runner(RuntimeError("experiment failed"))
        evaluator = SystemdFaultDetectionEvaluator(
            experiment_runner=runner,
            capture_factory=lambda _unit: capture,
            monotonic_usec_clock=_Clock((1_000,)),
            pre_fault_grace_seconds=0.0,
            post_recovery_grace_seconds=0.2,
            sleeper=lambda _seconds: None,
        )

        with self.assertRaises(RuntimeError):
            evaluator.run(_manifest(), _artifact())

        self.assertEqual(capture.grace_values, [0.0])

    def test_evaluator_rejects_manifest_artifact_mismatch(self) -> None:
        wrong = build_systemd_lab_fixture(
            SystemdLabFixtureSpec("other-01", runtime_max_seconds=120)
        )
        evaluator = SystemdFaultDetectionEvaluator(
            experiment_runner=_Runner(_experiment_run()),
            capture_factory=lambda _unit: _Capture(()),
        )

        with self.assertRaises(DetectionEvaluationContractError):
            evaluator.run(_manifest(), wrong)

    def test_evaluator_rejects_invalid_capture_contract_before_experiment(self) -> None:
        runner = _Runner(_experiment_run())
        evaluator = SystemdFaultDetectionEvaluator(
            experiment_runner=runner,
            capture_factory=lambda _unit: object(),
        )

        with self.assertRaises(DetectionEvaluationContractError):
            evaluator.run(_manifest(), _artifact())

        self.assertEqual(runner.calls, 0)

    def test_evaluator_rejects_invalid_clock_values(self) -> None:
        capture = _Capture((_sample(900, SystemdServiceHealthStatus.HEALTHY),))
        evaluator = SystemdFaultDetectionEvaluator(
            experiment_runner=_Runner(_experiment_run()),
            capture_factory=lambda _unit: capture,
            monotonic_usec_clock=lambda: True,
            pre_fault_grace_seconds=0.0,
            sleeper=lambda _seconds: None,
        )

        with self.assertRaises(DetectionEvaluationContractError):
            evaluator.run(_manifest(), _artifact())

    def test_evaluator_constructor_rejects_invalid_dependencies_and_grace(self) -> None:
        with self.assertRaises(TypeError):
            SystemdFaultDetectionEvaluator(experiment_runner=object())
        with self.assertRaises(TypeError):
            SystemdFaultDetectionEvaluator(capture_factory=object())
        with self.assertRaises(TypeError):
            SystemdFaultDetectionEvaluator(monotonic_usec_clock=None)
        with self.assertRaises(DetectionEvaluationContractError):
            SystemdFaultDetectionEvaluator(pre_fault_grace_seconds=-1)
        with self.assertRaises(DetectionEvaluationContractError):
            SystemdFaultDetectionEvaluator(post_recovery_grace_seconds=6)


if __name__ == "__main__":
    unittest.main()
