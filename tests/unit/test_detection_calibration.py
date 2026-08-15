"""Tests for empirical detector calibration evidence and artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from sentinel_x.detection.benchmarking import (
    DetectionIntegerMetricSummary,
    RepeatedDetectionBenchmark,
    RepeatedDetectionBenchmarkPlan,
    build_repeated_detection_record,
    build_repeated_detection_report,
)
from sentinel_x.detection.calibration import (
    DETECTION_CALIBRATION_SCHEMA_VERSION,
    DETECTION_CALIBRATION_VERSION,
    CalibrationClaimKind,
    CalibrationEvidenceStatus,
    CalibrationMetricAvailability,
    CalibrationMetricKind,
    CalibrationRateKind,
    CalibrationScopeKind,
    DetectionCalibrationArtifact,
    DetectionCalibrationClaim,
    DetectionCalibrationContractError,
    DetectionCalibrationMetricEvidence,
    DetectionCalibrationRateEvidence,
    DetectionCalibrationWriteError,
    build_detection_calibration,
    build_detection_calibration_artifact,
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

_UNIT = "sentinel-x-lab-detection-calibration-01.service"
_NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def _artifact():
    return build_systemd_lab_fixture(
        SystemdLabFixtureSpec("detection-calibration-01", runtime_max_seconds=120)
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
) -> FaultDetectionEvaluationRun:
    manifest = FaultExperimentManifest(
        FaultScenario(
            scenario_id=scenario_id,
            description="Detection calibration unit test.",
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
        ended_at=datetime(2026, 8, 15, 12, 0, 1, tzinfo=timezone.utc),
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


def _record(ordinal: int, mode: FaultMode):
    label = "inactive" if mode is FaultMode.SERVICE_INACTIVE else "failed"
    scenario_id = f"detectbench-{label}-r001"
    transition = None if mode is FaultMode.SERVICE_INACTIVE else 1_400
    evaluation_run = _evaluation_run(
        mode,
        scenario_id=scenario_id,
        experiment_id=f"exp-{ordinal:032x}",
        transition=transition,
    )
    return build_repeated_detection_record(
        ordinal=ordinal,
        fault_mode=mode,
        scenario_id=scenario_id,
        evaluation_run=evaluation_run,
    )


def _benchmark() -> RepeatedDetectionBenchmark:
    records = (
        _record(1, FaultMode.SERVICE_INACTIVE),
        _record(2, FaultMode.SERVICE_FAILED),
    )
    return RepeatedDetectionBenchmark(
        plan=RepeatedDetectionBenchmarkPlan(_UNIT, repetitions_per_mode=1),
        records=records,
        report=build_repeated_detection_report(records),
    )


def _canonical_json(value: object) -> str:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


class DetectionCalibrationTests(unittest.TestCase):
    def test_claim_supported_without_counterexample_or_missing_run(self) -> None:
        claim = DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.RUN_DETECTION_COVERAGE,
            observation_count=5,
            counterexample_count=0,
            incomplete_run_count=0,
        )
        self.assertIs(claim.status, CalibrationEvidenceStatus.SUPPORTED)

    def test_claim_counterexample_is_contradicted(self) -> None:
        claim = DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.RUN_DETECTION_COVERAGE,
            observation_count=5,
            counterexample_count=1,
            incomplete_run_count=0,
        )
        self.assertIs(claim.status, CalibrationEvidenceStatus.CONTRADICTED)

    def test_claim_missing_run_is_incomplete(self) -> None:
        claim = DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.HEALTHY_CONTROL_CLEANLINESS,
            observation_count=4,
            counterexample_count=0,
            incomplete_run_count=1,
        )
        self.assertIs(claim.status, CalibrationEvidenceStatus.INCOMPLETE)

    def test_claim_zero_observation_is_incomplete(self) -> None:
        claim = DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.GROUND_TRUTH_FAULT_STATE_CLASSIFICATION,
            observation_count=0,
            counterexample_count=0,
            incomplete_run_count=1,
        )
        self.assertIs(claim.status, CalibrationEvidenceStatus.INCOMPLETE)

    def test_claim_rejects_counterexample_above_observation(self) -> None:
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationClaim.from_counts(
                CalibrationClaimKind.RUN_DETECTION_COVERAGE,
                observation_count=1,
                counterexample_count=2,
                incomplete_run_count=0,
            )

    def test_claim_rejects_status_drift(self) -> None:
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationClaim(
                CalibrationClaimKind.RUN_DETECTION_COVERAGE,
                2,
                0,
                0,
                CalibrationEvidenceStatus.INCOMPLETE,
            )

    def test_claim_serialization_is_explicit(self) -> None:
        claim = DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.RUN_DETECTION_COVERAGE,
            observation_count=2,
            counterexample_count=0,
            incomplete_run_count=0,
        )
        self.assertEqual(claim.to_dict()["status"], "supported")
        json.dumps(claim.to_dict())

    def test_rate_evidence_preserves_counts_and_rate(self) -> None:
        evidence = DetectionCalibrationRateEvidence(
            CalibrationRateKind.GROUND_TRUTH_UNASSESSED,
            2,
            10,
        )
        self.assertEqual(evidence.rate, 0.2)

    def test_rate_evidence_empty_denominator_is_none(self) -> None:
        evidence = DetectionCalibrationRateEvidence(
            CalibrationRateKind.GROUND_TRUTH_UNASSESSED,
            0,
            0,
        )
        self.assertIsNone(evidence.rate)

    def test_rate_evidence_rejects_impossible_fraction(self) -> None:
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationRateEvidence(
                CalibrationRateKind.GROUND_TRUTH_UNASSESSED,
                2,
                1,
            )

    def test_metric_unavailable_preserves_missingness(self) -> None:
        summary = DetectionIntegerMetricSummary.from_values((None, None))
        evidence = DetectionCalibrationMetricEvidence.from_summary(
            CalibrationMetricKind.DETECTION_VISIBILITY_LATENCY_USEC,
            run_count=2,
            summary=summary,
        )
        self.assertIs(
            evidence.availability,
            CalibrationMetricAvailability.UNAVAILABLE,
        )

    def test_metric_partial_preserves_missingness(self) -> None:
        summary = DetectionIntegerMetricSummary.from_values((10, None))
        evidence = DetectionCalibrationMetricEvidence.from_summary(
            CalibrationMetricKind.DETECTION_VISIBILITY_LATENCY_USEC,
            run_count=2,
            summary=summary,
        )
        self.assertIs(evidence.availability, CalibrationMetricAvailability.PARTIAL)

    def test_metric_complete_preserves_distribution(self) -> None:
        summary = DetectionIntegerMetricSummary.from_values((10, 20))
        evidence = DetectionCalibrationMetricEvidence.from_summary(
            CalibrationMetricKind.RECOVERY_DETECTION_LATENCY_USEC,
            run_count=2,
            summary=summary,
        )
        self.assertIs(evidence.availability, CalibrationMetricAvailability.COMPLETE)
        self.assertEqual(evidence.summary.maximum, 20)

    def test_metric_rejects_run_count_mismatch(self) -> None:
        summary = DetectionIntegerMetricSummary.from_values((10, None))
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationMetricEvidence.from_summary(
                CalibrationMetricKind.RECOVERY_DETECTION_LATENCY_USEC,
                run_count=3,
                summary=summary,
            )

    def test_metric_rejects_negative_latency(self) -> None:
        summary = DetectionIntegerMetricSummary.from_values((-1,))
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationMetricEvidence.from_summary(
                CalibrationMetricKind.RECOVERY_DETECTION_LATENCY_USEC,
                run_count=1,
                summary=summary,
            )

    def test_signed_transition_offset_is_preserved(self) -> None:
        summary = DetectionIntegerMetricSummary.from_values((-10, 20))
        evidence = DetectionCalibrationMetricEvidence.from_summary(
            CalibrationMetricKind.DETECTION_TRANSITION_OFFSET_USEC,
            run_count=2,
            summary=summary,
        )
        self.assertEqual(evidence.summary.minimum, -10)

    def test_builder_rejects_untyped_benchmark(self) -> None:
        with self.assertRaises(DetectionCalibrationContractError):
            build_detection_calibration(object())

    def test_builder_creates_deterministic_identity(self) -> None:
        benchmark = _benchmark()
        first = build_detection_calibration(benchmark)
        second = build_detection_calibration(benchmark)
        self.assertEqual(first.calibration_id, second.calibration_id)
        self.assertEqual(first.benchmark_sha256, second.benchmark_sha256)

    def test_builder_uses_content_addressed_benchmark_digest(self) -> None:
        benchmark = _benchmark()
        report = build_detection_calibration(benchmark)
        benchmark_json = _canonical_json(benchmark.to_dict())
        self.assertEqual(
            report.benchmark_sha256,
            hashlib.sha256(benchmark_json.encode()).hexdigest(),
        )

    def test_builder_core_claims_are_supported_for_clean_benchmark(self) -> None:
        report = build_detection_calibration(_benchmark())
        self.assertTrue(report.controlled_lab_core_claims_supported)
        self.assertTrue(report.overall.core_claims_supported)
        self.assertTrue(report.service_inactive.core_claims_supported)
        self.assertTrue(report.service_failed.core_claims_supported)

    def test_builder_inactive_transition_latency_stays_unavailable(self) -> None:
        report = build_detection_calibration(_benchmark())
        evidence = report.service_inactive.detection_visibility_latency_usec
        self.assertIs(
            evidence.availability,
            CalibrationMetricAvailability.UNAVAILABLE,
        )
        self.assertEqual(evidence.summary.observed_count, 0)
        self.assertEqual(evidence.summary.missing_count, 1)

    def test_builder_failed_transition_latency_is_complete(self) -> None:
        report = build_detection_calibration(_benchmark())
        evidence = report.service_failed.detection_visibility_latency_usec
        self.assertIs(evidence.availability, CalibrationMetricAvailability.COMPLETE)
        self.assertEqual(evidence.summary.observed_count, 1)

    def test_builder_recovery_latency_is_complete_in_both_modes(self) -> None:
        report = build_detection_calibration(_benchmark())
        self.assertIs(
            report.service_inactive.recovery_detection_latency_usec.availability,
            CalibrationMetricAvailability.COMPLETE,
        )
        self.assertIs(
            report.service_failed.recovery_detection_latency_usec.availability,
            CalibrationMetricAvailability.COMPLETE,
        )

    def test_builder_unassessed_rate_is_descriptive_not_core_failure(self) -> None:
        report = build_detection_calibration(_benchmark())
        self.assertGreater(report.overall.ground_truth_unassessed.numerator, 0)
        self.assertTrue(report.overall.core_claims_supported)

    def test_builder_keeps_evaluation_and_ground_truth_unassessed_separate(
        self,
    ) -> None:
        report = build_detection_calibration(_benchmark())
        self.assertEqual(
            report.overall.evaluation_window_unassessed.kind,
            CalibrationRateKind.EVALUATION_WINDOW_UNASSESSED,
        )
        self.assertEqual(
            report.overall.ground_truth_unassessed.kind,
            CalibrationRateKind.GROUND_TRUTH_UNASSESSED,
        )

    def test_builder_report_serialization_disclaims_confidence_and_thresholds(
        self,
    ) -> None:
        payload = build_detection_calibration(_benchmark()).to_dict()
        self.assertFalse(payload["probabilistic_confidence_assigned"])
        self.assertFalse(payload["operational_thresholds_derived"])
        self.assertEqual(
            payload["evidence_scope"],
            "controlled_lab_empirical_observations",
        )

    def test_builder_schema_and_version_are_stable(self) -> None:
        payload = build_detection_calibration(_benchmark()).to_dict()
        self.assertEqual(
            payload["schema_version"],
            DETECTION_CALIBRATION_SCHEMA_VERSION,
        )
        self.assertEqual(payload["calibration_version"], DETECTION_CALIBRATION_VERSION)

    def test_builder_scope_kinds_are_exact(self) -> None:
        report = build_detection_calibration(_benchmark())
        self.assertIs(report.overall.kind, CalibrationScopeKind.OVERALL)
        self.assertIs(
            report.service_inactive.kind,
            CalibrationScopeKind.SERVICE_INACTIVE,
        )
        self.assertIs(report.service_failed.kind, CalibrationScopeKind.SERVICE_FAILED)

    def test_record_without_ground_truth_fault_state_makes_claim_incomplete(
        self,
    ) -> None:
        benchmark = _benchmark()
        inactive = replace(
            benchmark.records[0],
            ground_truth_fault_state_sample_count=0,
            ground_truth_correct_fault_state_detection_count=0,
            ground_truth_missed_fault_state_sample_count=0,
        )
        records = (inactive, benchmark.records[1])
        modified = RepeatedDetectionBenchmark(
            plan=benchmark.plan,
            records=records,
            report=build_repeated_detection_report(records),
        )
        report = build_detection_calibration(modified)
        self.assertIs(
            report.service_inactive.ground_truth_fault_state_classification.status,
            CalibrationEvidenceStatus.INCOMPLETE,
        )

    def test_record_missed_fault_state_contradicts_classification(self) -> None:
        benchmark = _benchmark()
        inactive = benchmark.records[0]
        modified_inactive = replace(
            inactive,
            ground_truth_correct_fault_state_detection_count=(
                inactive.ground_truth_correct_fault_state_detection_count - 1
            ),
            ground_truth_missed_fault_state_sample_count=1,
        )
        records = (modified_inactive, benchmark.records[1])
        modified = RepeatedDetectionBenchmark(
            plan=benchmark.plan,
            records=records,
            report=build_repeated_detection_report(records),
        )
        report = build_detection_calibration(modified)
        self.assertIs(
            report.service_inactive.ground_truth_fault_state_classification.status,
            CalibrationEvidenceStatus.CONTRADICTED,
        )
        self.assertFalse(report.controlled_lab_core_claims_supported)

    def test_report_rejects_calibration_id_digest_drift(self) -> None:
        report = build_detection_calibration(_benchmark())
        with self.assertRaises(DetectionCalibrationContractError):
            replace(
                report,
                calibration_id="cal-" + ("0" * 32),
            )

    def test_artifact_rejects_cross_file_target_drift(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        calibration = json.loads(artifact.calibration_json)
        calibration["target_unit"] = "other.service"
        calibration_json = _canonical_json(calibration)
        calibration_sha = hashlib.sha256(calibration_json.encode()).hexdigest()
        manifest = json.loads(artifact.manifest_json)
        manifest["files"]["calibration.json"]["sha256"] = calibration_sha
        manifest["files"]["calibration.json"]["bytes"] = len(
            calibration_json.encode("utf-8")
        )
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationArtifact(
                artifact.calibration_id,
                artifact.benchmark_json,
                calibration_json,
                _canonical_json(manifest),
                artifact.benchmark_sha256,
                calibration_sha,
            )

    def test_artifact_is_canonical_and_content_addressed(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        self.assertTrue(artifact.benchmark_json.endswith("\n"))
        self.assertTrue(artifact.calibration_json.endswith("\n"))
        self.assertTrue(artifact.manifest_json.endswith("\n"))
        self.assertEqual(
            artifact.benchmark_sha256,
            hashlib.sha256(artifact.benchmark_json.encode()).hexdigest(),
        )
        self.assertEqual(
            artifact.calibration_sha256,
            hashlib.sha256(artifact.calibration_json.encode()).hexdigest(),
        )

    def test_artifact_metadata_does_not_duplicate_file_bodies(self) -> None:
        payload = build_detection_calibration_artifact(_benchmark()).to_dict()
        self.assertNotIn("benchmark_json", payload)
        self.assertNotIn("calibration_json", payload)
        self.assertNotIn("manifest_json", payload)
        json.dumps(payload)

    def test_artifact_manifest_has_exact_files(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        manifest = json.loads(artifact.manifest_json)
        self.assertEqual(
            set(manifest["files"]),
            {"benchmark.json", "calibration.json"},
        )

    def test_artifact_rejects_benchmark_digest_drift(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationArtifact(
                artifact.calibration_id,
                artifact.benchmark_json,
                artifact.calibration_json,
                artifact.manifest_json,
                "0" * 64,
                artifact.calibration_sha256,
            )

    def test_artifact_rejects_calibration_digest_drift(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationArtifact(
                artifact.calibration_id,
                artifact.benchmark_json,
                artifact.calibration_json,
                artifact.manifest_json,
                artifact.benchmark_sha256,
                "0" * 64,
            )

    def test_artifact_rejects_manifest_digest_drift(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        manifest = json.loads(artifact.manifest_json)
        manifest["files"]["benchmark.json"]["sha256"] = "0" * 64
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationArtifact(
                artifact.calibration_id,
                artifact.benchmark_json,
                artifact.calibration_json,
                _canonical_json(manifest),
                artifact.benchmark_sha256,
                artifact.calibration_sha256,
            )

    def test_artifact_rejects_manifest_byte_count_drift(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        manifest = json.loads(artifact.manifest_json)
        manifest["files"]["calibration.json"]["bytes"] += 1
        with self.assertRaises(DetectionCalibrationContractError):
            DetectionCalibrationArtifact(
                artifact.calibration_id,
                artifact.benchmark_json,
                artifact.calibration_json,
                _canonical_json(manifest),
                artifact.benchmark_sha256,
                artifact.calibration_sha256,
            )

    def test_private_bundle_uses_0700_directory_and_0600_files(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o700)
            destination = artifact.write_private_bundle(root)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            for filename in ("benchmark.json", "calibration.json", "manifest.json"):
                path = destination / filename
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_private_bundle_refuses_existing_destination(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o700)
            artifact.write_private_bundle(root)
            with self.assertRaises(DetectionCalibrationWriteError):
                artifact.write_private_bundle(root)

    def test_private_bundle_refuses_symlink_parent(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            link = Path(root) / "link"
            source.mkdir(mode=0o700)
            link.symlink_to(source, target_is_directory=True)
            with self.assertRaises(DetectionCalibrationWriteError):
                artifact.write_private_bundle(link)

    def test_private_bundle_refuses_group_writable_parent(self) -> None:
        artifact = build_detection_calibration_artifact(_benchmark())
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o770)
            with self.assertRaises(DetectionCalibrationWriteError):
                artifact.write_private_bundle(root)


if __name__ == "__main__":
    unittest.main()
