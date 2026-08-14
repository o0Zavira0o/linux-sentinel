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

from sentinel_x.lab import (
    FaultDatasetArtifact,
    FaultDatasetBenchmarkReport,
    FaultDatasetModeReport,
    FaultDatasetContractError,
    FaultDatasetPlan,
    FaultDatasetRecord,
    FaultDatasetWriteError,
    FaultExperimentBenchmark,
    FaultExperimentDataset,
    FaultExperimentManifest,
    FaultExperimentRun,
    FaultGroundTruthWindow,
    FaultInjectionOutcome,
    FaultMetricSummary,
    FaultMode,
    LabFaultOperation,
    SystemdFaultDatasetRunner,
    SystemdLabFaultPlan,
    SystemdLabFixtureSpec,
    SystemdLabServiceState,
    build_fault_dataset_artifact,
    build_fault_dataset_report,
    build_systemd_lab_fixture,
)

_UNIT = "sentinel-x-lab-dataset-01.service"
_DATASET_ID = "dataset-0123456789abcdef0123456789abcdef"


def _state(active: str, sub: str, pid: int, result: str) -> SystemdLabServiceState:
    return SystemdLabServiceState(
        unit_name=_UNIT,
        load_state="loaded",
        active_state=active,
        sub_state=sub,
        main_pid=pid,
        result=result,
    )


def _run(
    mode: FaultMode,
    scenario_id: str,
    *,
    experiment_hex: str,
    transition: int | None = 2_000_000,
    service_latency: int | None = 100_000,
    journal_latency: int | None = None,
    correlated_latency: int | None = None,
    exact_latency: int | None = None,
    recovery_latency: int | None = 150_000,
    journal_count: int = 0,
    correlated_count: int = 0,
    exact_count: int = 0,
) -> FaultExperimentRun:
    experiment_id = f"exp-{experiment_hex}"
    truth = FaultGroundTruthWindow(
        experiment_id=experiment_id,
        scenario_id=scenario_id,
        target_unit=_UNIT,
        fault_mode=mode,
        started_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
        started_monotonic_usec=2_020_000,
        ended_at=datetime(2026, 8, 14, 10, 0, 1, tzinfo=timezone.utc),
        ended_monotonic_usec=3_020_000,
    )
    operation = (
        LabFaultOperation.STOP_SERVICE
        if mode is FaultMode.SERVICE_INACTIVE
        else LabFaultOperation.ABORT_MAIN_PROCESS
    )
    fault_active = "inactive" if mode is FaultMode.SERVICE_INACTIVE else "failed"
    plan = SystemdLabFaultPlan(
        experiment_id=experiment_id,
        scenario_id=scenario_id,
        target_unit=_UNIT,
        fault_mode=mode,
        operation=operation,
        expected_fault_active_states=(fault_active,),
        recovery_operations=("start", "reset-failed"),
        artifact_sha256="a" * 64,
    )
    outcome = FaultInjectionOutcome(
        plan=plan,
        ground_truth=truth,
        baseline_state=_state("active", "running", 10, "success"),
        fault_state=_state(
            fault_active,
            "dead" if mode is FaultMode.SERVICE_INACTIVE else "failed",
            0,
            "success" if mode is FaultMode.SERVICE_INACTIVE else "signal",
        ),
        recovered_state=_state("active", "running", 20, "success"),
    )
    benchmark = FaultExperimentBenchmark(
        experiment_id=experiment_id,
        scenario_id=scenario_id,
        target_unit=_UNIT,
        fault_mode=mode.value,
        ground_truth_duration_usec=1_000_000,
        telemetry_sample_count=0,
        fault_service_sample_count=0,
        fault_journal_entry_count=journal_count,
        fault_correlated_entry_count=correlated_count,
        fault_exact_invocation_entry_count=exact_count,
        reported_fault_transition_monotonic_usec=transition,
        ground_truth_confirmation_lag_usec=(None if transition is None else 20_000),
        service_fault_visibility_latency_usec=service_latency,
        journal_fault_evidence_latency_usec=journal_latency,
        correlated_fault_evidence_latency_usec=correlated_latency,
        exact_invocation_evidence_latency_usec=exact_latency,
        recovery_visibility_latency_usec=recovery_latency,
    )
    return FaultExperimentRun(outcome, benchmark, ())


def _records() -> tuple[FaultDatasetRecord, ...]:
    return (
        FaultDatasetRecord(
            1,
            _run(
                FaultMode.SERVICE_INACTIVE,
                "dataset-inactive-r001",
                experiment_hex="1" * 32,
                transition=None,
                service_latency=None,
                journal_count=2,
            ),
        ),
        FaultDatasetRecord(
            2,
            _run(
                FaultMode.SERVICE_FAILED,
                "dataset-failed-r001",
                experiment_hex="2" * 32,
                journal_count=4,
                correlated_count=4,
                exact_count=4,
            ),
        ),
    )


def _plan() -> FaultDatasetPlan:
    return FaultDatasetPlan(
        _UNIT,
        repetitions_per_mode=1,
        dataset_id=_DATASET_ID,
        created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )


def _dataset() -> FaultExperimentDataset:
    records = _records()
    return FaultExperimentDataset(
        _plan(),
        records,
        build_fault_dataset_report(_DATASET_ID, records),
    )


class FaultLabDatasetTests(unittest.TestCase):
    def test_plan_builds_balanced_deterministic_case_sequence(self) -> None:
        plan = FaultDatasetPlan(
            _UNIT,
            repetitions_per_mode=2,
            dataset_id=_DATASET_ID,
            created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
        )
        self.assertEqual(plan.expected_run_count, 4)
        self.assertEqual(
            plan.case_sequence(),
            (
                (1, FaultMode.SERVICE_INACTIVE, "dataset-inactive-r001"),
                (2, FaultMode.SERVICE_FAILED, "dataset-failed-r001"),
                (3, FaultMode.SERVICE_INACTIVE, "dataset-inactive-r002"),
                (4, FaultMode.SERVICE_FAILED, "dataset-failed-r002"),
            ),
        )
        json.dumps(plan.to_dict())

    def test_plan_rejects_invalid_repetition_bounds_and_boolean(self) -> None:
        for value in (True, 0, 65):
            with self.subTest(value=value):
                with self.assertRaises(FaultDatasetContractError):
                    FaultDatasetPlan(_UNIT, repetitions_per_mode=value)

    def test_plan_rejects_invalid_dataset_identity(self) -> None:
        with self.assertRaises(FaultDatasetContractError):
            FaultDatasetPlan(_UNIT, dataset_id="dataset-bad")

    def test_plan_rejects_naive_creation_time(self) -> None:
        with self.assertRaises(FaultDatasetContractError):
            FaultDatasetPlan(_UNIT, created_at=datetime(2026, 8, 14))

    def test_plan_rejects_unsafe_target(self) -> None:
        with self.assertRaises(ValueError):
            FaultDatasetPlan("ssh.service")

    def test_metric_summary_preserves_missingness_and_nearest_rank_p95(self) -> None:
        summary = FaultMetricSummary.from_values((None, 10, 20, 30, 40, 50))
        self.assertEqual(summary.observed_count, 5)
        self.assertEqual(summary.missing_count, 1)
        self.assertEqual(summary.minimum_usec, 10)
        self.assertEqual(summary.p50_usec, 30)
        self.assertEqual(summary.p95_usec, 50)
        self.assertEqual(summary.maximum_usec, 50)

    def test_metric_summary_empty_observations_stay_explicit(self) -> None:
        summary = FaultMetricSummary.from_values((None, None))
        self.assertEqual(summary.to_dict()["observed_count"], 0)
        self.assertIsNone(summary.p95_usec)

    def test_metric_summary_rejects_invented_empty_distribution(self) -> None:
        with self.assertRaises(FaultDatasetContractError):
            FaultMetricSummary(0, 1, 1, None, None, None)

    def test_metric_summary_rejects_regressing_distribution(self) -> None:
        with self.assertRaises(FaultDatasetContractError):
            FaultMetricSummary(1, 0, 2, 1, 2, 2)

    def test_record_serialization_carries_explicit_ground_truth_label(self) -> None:
        record = _records()[0]
        serialized = record.to_dict()
        self.assertEqual(serialized["ground_truth_label"], "service_inactive")
        self.assertEqual(serialized["label_source"], "injected_ground_truth")
        self.assertEqual(serialized["fault_mode"], "service_inactive")
        json.dumps(serialized)

    def test_report_aggregates_coverage_counts_and_missing_metrics(self) -> None:
        report = build_fault_dataset_report(_DATASET_ID, _records())
        self.assertEqual(report.total_run_count, 2)
        self.assertEqual(report.inactive_run_count, 1)
        self.assertEqual(report.failed_run_count, 1)
        self.assertEqual(report.total_fault_journal_entry_count, 6)
        self.assertEqual(report.total_fault_correlated_entry_count, 4)
        self.assertEqual(report.total_fault_exact_invocation_entry_count, 4)
        self.assertEqual(report.correlation_coverage_count, 1)
        self.assertEqual(report.exact_invocation_coverage_count, 1)
        self.assertEqual(report.service_fault_visibility_latency.observed_count, 1)
        self.assertEqual(report.service_fault_visibility_latency.missing_count, 1)
        self.assertEqual(report.correlation_coverage_rate, 0.5)
        self.assertEqual(
            report.inactive.service_fault_visibility_latency.observed_count, 0
        )
        self.assertEqual(
            report.failed.service_fault_visibility_latency.observed_count, 1
        )
        json.dumps(report.to_dict())

    def test_report_rejects_impossible_coverage_count(self) -> None:
        summary = FaultMetricSummary.from_values((1,))
        inactive = FaultDatasetModeReport(
            FaultMode.SERVICE_INACTIVE,
            1,
            1,
            0,
            0,
            0,
            0,
            0,
            summary,
            summary,
            summary,
            summary,
            summary,
            summary,
            summary,
        )
        failed = FaultDatasetModeReport(
            FaultMode.SERVICE_FAILED,
            0,
            0,
            0,
            0,
            0,
            0,
            0,
            FaultMetricSummary.from_values(()),
            FaultMetricSummary.from_values(()),
            FaultMetricSummary.from_values(()),
            FaultMetricSummary.from_values(()),
            FaultMetricSummary.from_values(()),
            FaultMetricSummary.from_values(()),
            FaultMetricSummary.from_values(()),
        )
        with self.assertRaises(FaultDatasetContractError):
            FaultDatasetBenchmarkReport(
                _DATASET_ID,
                1,
                1,
                0,
                2,
                0,
                0,
                0,
                0,
                0,
                inactive,
                failed,
                summary,
                summary,
                summary,
                summary,
                summary,
                summary,
                summary,
            )

    def test_dataset_requires_exact_plan_sequence(self) -> None:
        records = tuple(reversed(_records()))
        report = build_fault_dataset_report(_DATASET_ID, records)
        with self.assertRaises(FaultDatasetContractError):
            FaultExperimentDataset(_plan(), records, report)

    def test_dataset_rejects_duplicate_experiment_identity(self) -> None:
        first = _records()[0]
        duplicate = FaultDatasetRecord(
            2,
            _run(
                FaultMode.SERVICE_FAILED,
                "dataset-failed-r001",
                experiment_hex="1" * 32,
            ),
        )
        records = (first, duplicate)
        report = build_fault_dataset_report(_DATASET_ID, records)
        with self.assertRaises(FaultDatasetContractError):
            FaultExperimentDataset(_plan(), records, report)

    def test_dataset_rejects_report_drift(self) -> None:
        records = _records()
        report = build_fault_dataset_report(_DATASET_ID, records)
        drifted = replace(
            report,
            total_fault_journal_entry_count=7,
        )
        with self.assertRaises(FaultDatasetContractError):
            FaultExperimentDataset(_plan(), records, drifted)

    def test_dataset_metadata_serialization_does_not_duplicate_records(self) -> None:
        serialized = _dataset().to_dict()
        self.assertEqual(serialized["record_count"], 2)
        self.assertNotIn("records", serialized)
        json.dumps(serialized)

    def test_artifact_is_canonical_and_content_addressed(self) -> None:
        artifact = build_fault_dataset_artifact(_dataset())
        self.assertTrue(artifact.records_jsonl.endswith("\n"))
        self.assertTrue(artifact.report_json.endswith("\n"))
        self.assertEqual(
            artifact.records_sha256,
            hashlib.sha256(artifact.records_jsonl.encode()).hexdigest(),
        )
        self.assertEqual(len(artifact.records_jsonl.splitlines()), 2)
        first_record = json.loads(artifact.records_jsonl.splitlines()[0])
        self.assertEqual(first_record["dataset_id"], _DATASET_ID)
        manifest = json.loads(artifact.manifest_json)
        self.assertEqual(manifest["record_count"], 2)
        self.assertEqual(
            manifest["files"]["records.jsonl"]["sha256"],
            artifact.records_sha256,
        )

    def test_artifact_rejects_digest_mismatch(self) -> None:
        artifact = build_fault_dataset_artifact(_dataset())
        with self.assertRaises(FaultDatasetContractError):
            FaultDatasetArtifact(
                artifact.dataset_id,
                artifact.record_count,
                artifact.records_jsonl,
                artifact.report_json,
                artifact.manifest_json,
                "0" * 64,
                artifact.report_sha256,
            )

    def test_artifact_rejects_manifest_digest_drift(self) -> None:
        artifact = build_fault_dataset_artifact(_dataset())
        manifest = json.loads(artifact.manifest_json)
        manifest["files"]["records.jsonl"]["sha256"] = "0" * 64
        manifest_json = (
            json.dumps(
                manifest,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        )
        with self.assertRaises(FaultDatasetContractError):
            FaultDatasetArtifact(
                artifact.dataset_id,
                artifact.record_count,
                artifact.records_jsonl,
                artifact.report_json,
                manifest_json,
                artifact.records_sha256,
                artifact.report_sha256,
            )

    def test_artifact_rejects_record_count_drift(self) -> None:
        artifact = build_fault_dataset_artifact(_dataset())
        with self.assertRaises(FaultDatasetContractError):
            FaultDatasetArtifact(
                artifact.dataset_id,
                artifact.record_count + 1,
                artifact.records_jsonl,
                artifact.report_json,
                artifact.manifest_json,
                artifact.records_sha256,
                artifact.report_sha256,
            )

    def test_private_bundle_uses_0700_directory_and_0600_files(self) -> None:
        artifact = build_fault_dataset_artifact(_dataset())
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o700)
            destination = artifact.write_private_bundle(root)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)
            for filename in ("records.jsonl", "report.json", "manifest.json"):
                path = destination / filename
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_private_bundle_refuses_existing_destination(self) -> None:
        artifact = build_fault_dataset_artifact(_dataset())
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o700)
            artifact.write_private_bundle(root)
            with self.assertRaises(FaultDatasetWriteError):
                artifact.write_private_bundle(root)

    def test_private_bundle_refuses_symlink_parent(self) -> None:
        artifact = build_fault_dataset_artifact(_dataset())
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            link = Path(root) / "link"
            source.mkdir(mode=0o700)
            link.symlink_to(source, target_is_directory=True)
            with self.assertRaises(FaultDatasetWriteError):
                artifact.write_private_bundle(link)

    def test_private_bundle_refuses_group_writable_parent(self) -> None:
        artifact = build_fault_dataset_artifact(_dataset())
        with tempfile.TemporaryDirectory() as root:
            os.chmod(root, 0o770)
            with self.assertRaises(FaultDatasetWriteError):
                artifact.write_private_bundle(root)

    def test_runner_rejects_target_artifact_mismatch_before_execution(self) -> None:
        plan = _plan()
        other = build_systemd_lab_fixture(SystemdLabFixtureSpec("other-01"))
        orchestrator = _FakeOrchestrator()
        runner = SystemdFaultDatasetRunner(orchestrator=orchestrator)
        with self.assertRaises(FaultDatasetContractError):
            runner.run(plan, other)
        self.assertEqual(orchestrator.manifests, [])

    def test_runner_executes_exact_balanced_sequence_and_builds_dataset(self) -> None:
        orchestrator = _FakeOrchestrator()
        runner = SystemdFaultDatasetRunner(orchestrator=orchestrator)
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("dataset-01"))
        dataset = runner.run(_plan(), artifact)
        self.assertEqual(len(dataset.records), 2)
        self.assertEqual(
            [manifest.scenario.scenario_id for manifest in orchestrator.manifests],
            ["dataset-inactive-r001", "dataset-failed-r001"],
        )
        self.assertEqual(
            [manifest.scenario.fault_mode for manifest in orchestrator.manifests],
            [FaultMode.SERVICE_INACTIVE, FaultMode.SERVICE_FAILED],
        )
        self.assertEqual(dataset.report.total_run_count, 2)

    def test_runner_stops_immediately_when_an_experiment_fails(self) -> None:
        orchestrator = _FakeOrchestrator(fail_on_call=2)
        runner = SystemdFaultDatasetRunner(orchestrator=orchestrator)
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("dataset-01"))
        with self.assertRaisesRegex(RuntimeError, "experiment failed"):
            runner.run(_plan(), artifact)
        self.assertEqual(len(orchestrator.manifests), 2)

    def test_runner_rejects_invalid_orchestrator_contract(self) -> None:
        with self.assertRaises(TypeError):
            SystemdFaultDatasetRunner(orchestrator=object())


class _FakeOrchestrator:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.manifests: list[FaultExperimentManifest] = []
        self.fail_on_call = fail_on_call

    def run(self, manifest, artifact):
        self.manifests.append(manifest)
        if self.fail_on_call == len(self.manifests):
            raise RuntimeError("experiment failed")
        mode = manifest.scenario.fault_mode
        digit = "1" if mode is FaultMode.SERVICE_INACTIVE else "2"
        return _run(
            mode,
            manifest.scenario.scenario_id,
            experiment_hex=digit * 32,
            transition=(None if mode is FaultMode.SERVICE_INACTIVE else 2_000_000),
            service_latency=(None if mode is FaultMode.SERVICE_INACTIVE else 100_000),
            journal_count=2 if mode is FaultMode.SERVICE_INACTIVE else 4,
            correlated_count=0 if mode is FaultMode.SERVICE_INACTIVE else 4,
            exact_count=0 if mode is FaultMode.SERVICE_INACTIVE else 4,
        )


if __name__ == "__main__":
    unittest.main()
