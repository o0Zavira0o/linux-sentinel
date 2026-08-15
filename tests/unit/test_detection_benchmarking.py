"""Tests for repeated statistical detector benchmarking."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from sentinel_x.detection.benchmarking import (
    DetectionIntegerMetricSummary,
    RepeatedDetectionBenchmark,
    RepeatedDetectionBenchmarkContractError,
    RepeatedDetectionBenchmarkPlan,
    RepeatedDetectionBenchmarkRecord,
    RepeatedDetectionBenchmarkReport,
    SystemdRepeatedDetectionBenchmarkRunner,
    build_repeated_detection_record,
    build_repeated_detection_report,
)
from sentinel_x.detection.evaluation import (
    DetectionEvaluationSample,
    FaultDetectionEvaluationRun,
    build_fault_detection_benchmark,
)
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
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

_UNIT = "sentinel-x-lab-detection-benchmark-01.service"
_NOW = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)


def _artifact():
    return build_systemd_lab_fixture(
        SystemdLabFixtureSpec("detection-benchmark-01", runtime_max_seconds=120)
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


def _sample(
    assessed: int,
    status: SystemdServiceHealthStatus,
    *,
    active: str | None = None,
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
        source_event_id=f"event-{assessed}-{status.value}",
        assessment_id=f"asmt-{assessed}-{status.value}",
        target_unit=_UNIT,
        status=status,
        anomaly_class=anomaly,
        load_state="loaded",
        active_state=resolved_active,
        sub_state=sub,
        main_pid=pid,
        state_change_monotonic_usec=None,
    )


def _evaluation_run(
    mode: FaultMode,
    *,
    scenario_id: str,
    experiment_id: str,
    transition: int | None,
    samples: tuple[DetectionEvaluationSample, ...] | None = None,
) -> FaultDetectionEvaluationRun:
    manifest = FaultExperimentManifest(
        FaultScenario(
            scenario_id=scenario_id,
            description="Repeated detection benchmark unit test.",
            target_unit=_UNIT,
            fault_mode=mode,
            evidence_grace_seconds=1.0,
        ),
        experiment_id=experiment_id,
        created_at=_NOW,
    )
    truth = FaultGroundTruthWindow.from_manifest(
        manifest,
        started_at=_NOW,
        started_monotonic_usec=1_500,
    ).close(
        ended_at=datetime(2026, 8, 14, 20, 0, 1, tzinfo=timezone.utc),
        ended_monotonic_usec=2_800,
    )
    expected_active = "inactive" if mode is FaultMode.SERVICE_INACTIVE else "failed"
    operation = (
        LabFaultOperation.STOP_SERVICE
        if mode is FaultMode.SERVICE_INACTIVE
        else LabFaultOperation.ABORT_MAIN_PROCESS
    )
    outcome = FaultInjectionOutcome(
        plan=SystemdLabFaultPlan(
            experiment_id=experiment_id,
            scenario_id=scenario_id,
            target_unit=_UNIT,
            fault_mode=mode,
            operation=operation,
            expected_fault_active_states=(expected_active,),
            recovery_operations=("start", "reset-failed"),
            artifact_sha256=_artifact().sha256,
        ),
        ground_truth=truth,
        baseline_state=_state("active", "running", 10, "success"),
        fault_state=_state(
            expected_active,
            "dead" if expected_active == "inactive" else "failed",
            0,
            "success" if expected_active == "inactive" else "signal",
        ),
        recovered_state=_state("active", "running", 20, "success"),
    )
    telemetry = (
        FaultExperimentTelemetrySample(
            observed_monotonic_usec=1_600,
            service_event_id="service-1600",
            service_active_state=expected_active,
            service_sub_state=("dead" if expected_active == "inactive" else "failed"),
            service_main_pid=None,
            service_state_change_monotonic_usec=transition,
            journal_event_id=None,
            journal_entry_monotonic_usec=(),
            correlated_entry_monotonic_usec=(),
            exact_invocation_entry_monotonic_usec=(),
        ),
    )
    experiment_run = FaultExperimentRun(
        outcome=outcome,
        benchmark=FaultExperimentBenchmark(
            experiment_id=experiment_id,
            scenario_id=scenario_id,
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
            service_fault_visibility_latency_usec=(None if transition is None else 50),
            journal_fault_evidence_latency_usec=None,
            correlated_fault_evidence_latency_usec=None,
            exact_invocation_evidence_latency_usec=None,
            recovery_visibility_latency_usec=100,
        ),
        telemetry_samples=telemetry,
    )
    expected_status = (
        SystemdServiceHealthStatus.INACTIVE
        if mode is FaultMode.SERVICE_INACTIVE
        else SystemdServiceHealthStatus.FAILED
    )
    if samples is None:
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_100, SystemdServiceHealthStatus.UNASSESSED),
            _sample(1_450, expected_status),
            _sample(1_600, expected_status),
            _sample(2_200, expected_status),
            _sample(2_750, SystemdServiceHealthStatus.UNASSESSED),
            _sample(2_900, SystemdServiceHealthStatus.HEALTHY),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )
    benchmark = build_fault_detection_benchmark(
        experiment_run,
        samples,
        evaluation_started_monotonic_usec=1_000,
        recovery_confirmed_monotonic_usec=3_000,
    )
    return FaultDetectionEvaluationRun(
        experiment_run=experiment_run,
        detection_samples=samples,
        benchmark=benchmark,
    )


def _record(
    ordinal: int,
    mode: FaultMode,
    *,
    transition: int | None = None,
    samples: tuple[DetectionEvaluationSample, ...] | None = None,
) -> RepeatedDetectionBenchmarkRecord:
    label = "inactive" if mode is FaultMode.SERVICE_INACTIVE else "failed"
    scenario = f"detectbench-{label}-r{((ordinal + 1) // 2):03d}"
    experiment_id = f"exp-{ordinal:032x}"
    if transition is None and mode is FaultMode.SERVICE_FAILED:
        transition = 1_400
    run = _evaluation_run(
        mode,
        scenario_id=scenario,
        experiment_id=experiment_id,
        transition=transition,
        samples=samples,
    )
    return build_repeated_detection_record(
        ordinal=ordinal,
        fault_mode=mode,
        scenario_id=scenario,
        evaluation_run=run,
    )


class _FakeEvaluator:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[tuple[str, FaultMode]] = []
        self.fail_on_call = fail_on_call

    def run(self, manifest, artifact):
        del artifact
        self.calls.append((manifest.scenario.scenario_id, manifest.scenario.fault_mode))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("synthetic evaluator failure")
        transition = (
            None
            if manifest.scenario.fault_mode is FaultMode.SERVICE_INACTIVE
            else 1_400
        )
        return _evaluation_run(
            manifest.scenario.fault_mode,
            scenario_id=manifest.scenario.scenario_id,
            experiment_id=manifest.experiment_id,
            transition=transition,
        )


class RepeatedDetectionBenchmarkTests(unittest.TestCase):
    def test_plan_defaults_to_balanced_ten_run_sequence(self):
        plan = RepeatedDetectionBenchmarkPlan(_UNIT)
        self.assertEqual(plan.expected_run_count, 10)
        self.assertEqual(plan.repetitions_per_mode, 5)
        self.assertEqual(plan.case_sequence()[0][2], "detectbench-inactive-r001")
        self.assertEqual(plan.case_sequence()[1][2], "detectbench-failed-r001")
        self.assertEqual(plan.case_sequence()[-1][2], "detectbench-failed-r005")

    def test_plan_sequence_is_strictly_alternating_and_ordinal(self):
        plan = RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=3)
        self.assertEqual(
            [(case[0], case[1]) for case in plan.case_sequence()],
            [
                (1, FaultMode.SERVICE_INACTIVE),
                (2, FaultMode.SERVICE_FAILED),
                (3, FaultMode.SERVICE_INACTIVE),
                (4, FaultMode.SERVICE_FAILED),
                (5, FaultMode.SERVICE_INACTIVE),
                (6, FaultMode.SERVICE_FAILED),
            ],
        )

    def test_plan_rejects_boolean_and_out_of_bounds_repetitions(self):
        for value in (True, 0, 33):
            with self.subTest(value=value):
                with self.assertRaises(RepeatedDetectionBenchmarkContractError):
                    RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=value)

    def test_plan_rejects_unsafe_target_unit(self):
        with self.assertRaises(ValueError):
            RepeatedDetectionBenchmarkPlan("ssh.service")

    def test_plan_rejects_invalid_timeout_values(self):
        for field_name, value in (
            ("evidence_grace_seconds", 0),
            ("baseline_timeout_seconds", False),
            ("fault_timeout_seconds", float("inf")),
            ("recovery_timeout_seconds", 301),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(RepeatedDetectionBenchmarkContractError):
                    RepeatedDetectionBenchmarkPlan(_UNIT, **{field_name: value})

    def test_plan_rejects_evidence_grace_above_recovery_timeout(self):
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            RepeatedDetectionBenchmarkPlan(
                _UNIT,
                evidence_grace_seconds=21.0,
                recovery_timeout_seconds=20.0,
            )

    def test_plan_serialization_is_stable_and_json_friendly(self):
        plan = RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=1)
        payload = plan.to_dict()
        self.assertEqual(payload["expected_run_count"], 2)
        self.assertEqual(len(payload["case_sequence"]), 2)
        json.dumps(payload)

    def test_metric_summary_preserves_signed_values_and_missingness(self):
        summary = DetectionIntegerMetricSummary.from_values((-50, None, 10, 40))
        self.assertEqual(summary.observed_count, 3)
        self.assertEqual(summary.missing_count, 1)
        self.assertEqual(summary.minimum, -50)
        self.assertEqual(summary.p50, 10)
        self.assertEqual(summary.p95, 40)

    def test_metric_summary_uses_nearest_rank_p95(self):
        summary = DetectionIntegerMetricSummary.from_values(tuple(range(1, 11)))
        self.assertEqual(summary.p50, 5)
        self.assertEqual(summary.p95, 10)

    def test_metric_summary_all_missing_does_not_invent_distribution(self):
        summary = DetectionIntegerMetricSummary.from_values((None, None))
        self.assertEqual(summary.observed_count, 0)
        self.assertEqual(summary.missing_count, 2)
        self.assertIsNone(summary.minimum)
        self.assertIsNone(summary.maximum)

    def test_metric_summary_nonnegative_mode_rejects_signed_latency(self):
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            DetectionIntegerMetricSummary.from_values(
                (10, -1),
                require_nonnegative=True,
            )

    def test_metric_summary_rejects_boolean_metric(self):
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            DetectionIntegerMetricSummary.from_values((True,))

    def test_metric_summary_model_rejects_invented_empty_distribution(self):
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            DetectionIntegerMetricSummary(0, 2, 1, 1, 1, 1)

    def test_record_projects_ground_truth_active_interval_separately(self):
        record = _record(2, FaultMode.SERVICE_FAILED, transition=1_400)
        self.assertEqual(record.ground_truth_active_sample_count, 3)
        self.assertEqual(record.ground_truth_fault_state_sample_count, 2)
        self.assertEqual(record.ground_truth_correct_fault_state_detection_count, 2)
        self.assertEqual(record.ground_truth_missed_fault_state_sample_count, 0)
        self.assertEqual(record.ground_truth_unassessed_count, 1)
        self.assertEqual(record.ground_truth_other_anomaly_count, 0)

    def test_record_inactive_mapping_uses_inactive_fault_state(self):
        record = _record(1, FaultMode.SERVICE_INACTIVE, transition=None)
        self.assertEqual(record.ground_truth_fault_state_sample_count, 2)
        self.assertEqual(record.ground_truth_correct_fault_state_detection_count, 2)

    def test_record_excludes_pre_and_post_ground_truth_samples(self):
        record = _record(2, FaultMode.SERVICE_FAILED, transition=1_400)
        self.assertEqual(record.evaluation_run.benchmark.detection_sample_count, 8)
        self.assertEqual(record.ground_truth_active_sample_count, 3)

    def test_record_counts_ground_truth_fault_state_miss_explicitly(self):
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_600, SystemdServiceHealthStatus.UNASSESSED, active="failed"),
            _sample(2_200, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )
        record = _record(
            2,
            FaultMode.SERVICE_FAILED,
            transition=1_400,
            samples=samples,
        )
        self.assertEqual(record.ground_truth_fault_state_sample_count, 2)
        self.assertEqual(record.ground_truth_correct_fault_state_detection_count, 1)
        self.assertEqual(record.ground_truth_missed_fault_state_sample_count, 1)

    def test_record_counts_ground_truth_other_anomaly_separately(self):
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_600, SystemdServiceHealthStatus.FAILED),
            _sample(2_000, SystemdServiceHealthStatus.INACTIVE),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )
        record = _record(
            2,
            FaultMode.SERVICE_FAILED,
            transition=1_400,
            samples=samples,
        )
        self.assertEqual(record.ground_truth_other_anomaly_count, 1)

    def test_record_can_preserve_missing_ground_truth_sampling(self):
        samples = (
            _sample(900, SystemdServiceHealthStatus.HEALTHY),
            _sample(1_450, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )
        record = _record(
            2,
            FaultMode.SERVICE_FAILED,
            transition=1_400,
            samples=samples,
        )
        self.assertFalse(record.ground_truth_active_observed)
        self.assertEqual(record.ground_truth_active_sample_count, 0)

    def test_record_serialization_omits_raw_detection_sample_stream(self):
        payload = _record(2, FaultMode.SERVICE_FAILED).to_dict()
        self.assertNotIn("detection_samples", payload)
        self.assertIn("detection_benchmark", payload)
        json.dumps(payload)

    def test_record_rejects_mode_drift(self):
        run = _evaluation_run(
            FaultMode.SERVICE_FAILED,
            scenario_id="detectbench-failed-r001",
            experiment_id="exp-00000000000000000000000000000001",
            transition=1_400,
        )
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            build_repeated_detection_record(
                ordinal=1,
                fault_mode=FaultMode.SERVICE_INACTIVE,
                scenario_id="detectbench-failed-r001",
                evaluation_run=run,
            )

    def test_report_aggregates_overall_and_per_mode_counts(self):
        records = (
            _record(1, FaultMode.SERVICE_INACTIVE),
            _record(2, FaultMode.SERVICE_FAILED),
        )
        report = build_repeated_detection_report(records)
        self.assertEqual(report.overall.run_count, 2)
        self.assertEqual(report.service_inactive.run_count, 1)
        self.assertEqual(report.service_failed.run_count, 1)
        self.assertEqual(report.overall.detection_covered_run_count, 2)
        self.assertEqual(report.overall.total_missed_fault_state_sample_count, 0)

    def test_report_preserves_mode_specific_transition_missingness(self):
        records = (
            _record(1, FaultMode.SERVICE_INACTIVE, transition=None),
            _record(2, FaultMode.SERVICE_FAILED, transition=1_400),
        )
        report = build_repeated_detection_report(records)
        self.assertEqual(report.service_inactive.transition_reported_run_count, 0)
        self.assertEqual(
            report.service_inactive.detection_visibility_latency_usec.missing_count,
            1,
        )
        self.assertEqual(report.service_failed.transition_reported_run_count, 1)
        self.assertEqual(
            report.service_failed.detection_visibility_latency_usec.observed_count,
            1,
        )

    def test_report_preserves_negative_ground_truth_confirmation_offsets(self):
        report = build_repeated_detection_report(
            (
                _record(1, FaultMode.SERVICE_INACTIVE),
                _record(2, FaultMode.SERVICE_FAILED),
            )
        )
        summary = report.overall.ground_truth_confirmation_offset_usec
        self.assertLess(summary.minimum, 0)
        self.assertLess(summary.maximum, 0)

    def test_report_aggregates_healthy_control_false_positive_rate(self):
        samples = (
            _sample(900, SystemdServiceHealthStatus.INACTIVE),
            _sample(1_600, SystemdServiceHealthStatus.FAILED),
            _sample(3_100, SystemdServiceHealthStatus.HEALTHY),
        )
        record = _record(
            2,
            FaultMode.SERVICE_FAILED,
            transition=1_400,
            samples=samples,
        )
        report = build_repeated_detection_report((record,))
        self.assertEqual(report.overall.total_healthy_control_false_positive_count, 1)
        self.assertEqual(report.overall.healthy_control_false_positive_rate, 0.5)

    def test_report_keeps_fault_window_and_ground_truth_unassessed_separate(self):
        record = _record(2, FaultMode.SERVICE_FAILED)
        report = build_repeated_detection_report((record,)).overall
        self.assertEqual(report.total_fault_window_unassessed_count, 2)
        self.assertEqual(report.total_ground_truth_unassessed_count, 1)

    def test_report_ground_truth_accuracy_is_sample_state_conditioned(self):
        report = build_repeated_detection_report(
            (_record(2, FaultMode.SERVICE_FAILED),)
        ).overall
        self.assertEqual(report.ground_truth_fault_state_sample_accuracy, 1.0)
        self.assertEqual(report.fault_state_sample_accuracy, 1.0)

    def test_report_rejects_empty_record_set(self):
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            build_repeated_detection_report(())

    def test_scope_report_rejects_visibility_summary_count_drift(self):
        report = build_repeated_detection_report(
            (_record(2, FaultMode.SERVICE_FAILED),)
        ).overall
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            replace(
                report,
                transition_latency_measurable_run_count=0,
            )

    def test_repeated_benchmark_requires_exact_plan_sequence(self):
        plan = RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=1)
        records = (
            _record(2, FaultMode.SERVICE_FAILED),
            _record(1, FaultMode.SERVICE_INACTIVE),
        )
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            RepeatedDetectionBenchmark(
                plan=plan,
                records=records,
                report=build_repeated_detection_report(records),
            )

    def test_repeated_benchmark_rejects_duplicate_experiment_identity(self):
        plan = RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=1)
        first = _record(1, FaultMode.SERVICE_INACTIVE)
        second = _record(2, FaultMode.SERVICE_FAILED)
        duplicate_run = _evaluation_run(
            FaultMode.SERVICE_FAILED,
            scenario_id=second.scenario_id,
            experiment_id=first.experiment_id,
            transition=1_400,
        )
        duplicate = build_repeated_detection_record(
            ordinal=2,
            fault_mode=FaultMode.SERVICE_FAILED,
            scenario_id=second.scenario_id,
            evaluation_run=duplicate_run,
        )
        records = (first, duplicate)
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            RepeatedDetectionBenchmark(
                plan=plan,
                records=records,
                report=build_repeated_detection_report(records),
            )

    def test_repeated_benchmark_serialization_is_bounded_and_json_friendly(self):
        plan = RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=1)
        records = (
            _record(1, FaultMode.SERVICE_INACTIVE),
            _record(2, FaultMode.SERVICE_FAILED),
        )
        benchmark = RepeatedDetectionBenchmark(
            plan=plan,
            records=records,
            report=build_repeated_detection_report(records),
        )
        payload = benchmark.to_dict()
        self.assertNotIn("detection_samples", json.dumps(payload))
        json.dumps(payload)

    def test_runner_executes_exact_balanced_sequence_and_builds_report(self):
        evaluator = _FakeEvaluator()
        runner = SystemdRepeatedDetectionBenchmarkRunner(evaluator)
        result = runner.run(
            RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=2),
            _artifact(),
        )
        self.assertEqual(len(result.records), 4)
        self.assertEqual(
            evaluator.calls,
            [
                ("detectbench-inactive-r001", FaultMode.SERVICE_INACTIVE),
                ("detectbench-failed-r001", FaultMode.SERVICE_FAILED),
                ("detectbench-inactive-r002", FaultMode.SERVICE_INACTIVE),
                ("detectbench-failed-r002", FaultMode.SERVICE_FAILED),
            ],
        )
        self.assertEqual(result.report.overall.run_count, 4)

    def test_runner_stops_immediately_when_evaluation_fails(self):
        evaluator = _FakeEvaluator(fail_on_call=2)
        runner = SystemdRepeatedDetectionBenchmarkRunner(evaluator)
        with self.assertRaisesRegex(RuntimeError, "synthetic evaluator failure"):
            runner.run(
                RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=2),
                _artifact(),
            )
        self.assertEqual(len(evaluator.calls), 2)

    def test_runner_rejects_artifact_target_mismatch_before_evaluation(self):
        evaluator = _FakeEvaluator()
        runner = SystemdRepeatedDetectionBenchmarkRunner(evaluator)
        wrong = build_systemd_lab_fixture(
            SystemdLabFixtureSpec("other-benchmark", runtime_max_seconds=120)
        )
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            runner.run(RepeatedDetectionBenchmarkPlan(_UNIT), wrong)
        self.assertEqual(evaluator.calls, [])

    def test_runner_rejects_invalid_evaluator_contract(self):
        with self.assertRaises(TypeError):
            SystemdRepeatedDetectionBenchmarkRunner(object())

    def test_report_to_dict_contains_overall_and_both_modes(self):
        report = build_repeated_detection_report(
            (
                _record(1, FaultMode.SERVICE_INACTIVE),
                _record(2, FaultMode.SERVICE_FAILED),
            )
        )
        payload = report.to_dict()
        self.assertEqual(
            set(payload),
            {"overall", "service_inactive", "service_failed"},
        )
        json.dumps(payload)

    def test_report_model_rejects_per_mode_run_count_drift(self):
        report = build_repeated_detection_report(
            (
                _record(1, FaultMode.SERVICE_INACTIVE),
                _record(2, FaultMode.SERVICE_FAILED),
            )
        )
        with self.assertRaises(RepeatedDetectionBenchmarkContractError):
            RepeatedDetectionBenchmarkReport(
                overall=replace(report.overall, run_count=3),
                service_inactive=report.service_inactive,
                service_failed=report.service_failed,
            )


if __name__ == "__main__":
    unittest.main()
