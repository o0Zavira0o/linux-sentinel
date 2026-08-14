"""Reproducible labeled dataset and benchmark artifacts for the fault laboratory."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol
from uuid import uuid4

from sentinel_x.lab.fixture import (
    SystemdLabFixtureArtifact,
    validate_lab_fixture_unit_name,
)
from sentinel_x.lab.models import FaultExperimentManifest, FaultMode, FaultScenario
from sentinel_x.lab.orchestrator import (
    FaultExperimentRun,
    SystemdFaultExperimentOrchestrator,
)

_DATASET_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"dataset-[0-9a-f]{32}")
_SCHEMA_VERSION: Final[str] = "sentinel-x.fault-dataset.v1"
_MIN_REPETITIONS: Final[int] = 1
_MAX_REPETITIONS: Final[int] = 64
_MAX_TOTAL_RUNS: Final[int] = 128
_RECORDS_FILENAME: Final[str] = "records.jsonl"
_REPORT_FILENAME: Final[str] = "report.json"
_MANIFEST_FILENAME: Final[str] = "manifest.json"
_MAX_RECORDS_BYTES: Final[int] = 64 * 1024 * 1024
_MAX_METADATA_BYTES: Final[int] = 1024 * 1024
_FORBIDDEN_STORAGE_ROOTS: Final[tuple[Path, ...]] = (
    Path("/run/systemd/system"),
    Path("/etc/systemd/system"),
    Path("/usr/lib/systemd/system"),
    Path("/lib/systemd/system"),
)


class FaultDatasetError(RuntimeError):
    """Base error for fault-laboratory dataset construction and persistence."""


class FaultDatasetContractError(FaultDatasetError):
    """Raised when dataset inputs or derived statistics are inconsistent."""


class FaultDatasetWriteError(FaultDatasetError):
    """Raised when a dataset bundle cannot be written safely."""


class DatasetExperimentRunner(Protocol):
    """Structural experiment runner dependency used by the dataset runner."""

    def run(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultExperimentRun:
        """Execute one complete ground-truth-aligned experiment."""

        ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_dataset_id() -> str:
    return f"dataset-{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class FaultDatasetPlan:
    """Immutable reproducible plan for a balanced two-mode fault dataset."""

    target_unit: str
    repetitions_per_mode: int = 5
    evidence_grace_seconds: float = 1.0
    baseline_timeout_seconds: float = 10.0
    fault_timeout_seconds: float = 10.0
    recovery_timeout_seconds: float = 20.0
    dataset_id: str = field(default_factory=_new_dataset_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_lab_fixture_unit_name(self.target_unit)
        _validate_dataset_id(self.dataset_id)
        _validate_aware_datetime(self.created_at, field_name="created_at")
        if isinstance(self.repetitions_per_mode, bool) or not isinstance(
            self.repetitions_per_mode, int
        ):
            raise FaultDatasetContractError("repetitions_per_mode must be an integer")
        if not _MIN_REPETITIONS <= self.repetitions_per_mode <= _MAX_REPETITIONS:
            raise FaultDatasetContractError(
                "repetitions_per_mode must be between "
                f"{_MIN_REPETITIONS} and {_MAX_REPETITIONS}"
            )
        if self.expected_run_count > _MAX_TOTAL_RUNS:
            raise FaultDatasetContractError(
                "dataset run count exceeds the safety bound"
            )
        for field_name in (
            "evidence_grace_seconds",
            "baseline_timeout_seconds",
            "fault_timeout_seconds",
            "recovery_timeout_seconds",
        ):
            normalized = _validate_positive_seconds(
                getattr(self, field_name), field_name=field_name
            )
            object.__setattr__(self, field_name, normalized)
        if self.evidence_grace_seconds > self.recovery_timeout_seconds:
            raise FaultDatasetContractError(
                "evidence_grace_seconds must not exceed recovery_timeout_seconds"
            )

    @property
    def expected_run_count(self) -> int:
        """Return the exact balanced run count implied by this plan."""

        return self.repetitions_per_mode * 2

    def case_sequence(self) -> tuple[tuple[int, FaultMode, str], ...]:
        """Return deterministic alternating inactive/failed scenario identities."""

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
                        f"dataset-{label}-r{repetition:03d}",
                    )
                )
                ordinal += 1
        return tuple(cases)

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly dataset plan."""

        return {
            "schema_version": _SCHEMA_VERSION,
            "dataset_id": self.dataset_id,
            "created_at": self.created_at.isoformat(),
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
class FaultMetricSummary:
    """Nearest-rank p50/p95 integer summary with explicit missingness."""

    observed_count: int
    missing_count: int
    minimum_usec: int | None
    p50_usec: int | None
    p95_usec: int | None
    maximum_usec: int | None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("observed_count", self.observed_count),
            ("missing_count", self.missing_count),
        ):
            _validate_nonnegative_int(value, field_name=field_name)
        if self.observed_count == 0:
            if any(
                value is not None
                for value in (
                    self.minimum_usec,
                    self.p50_usec,
                    self.p95_usec,
                    self.maximum_usec,
                )
            ):
                raise FaultDatasetContractError(
                    "empty metric summaries must not invent distribution values"
                )
            return
        values = (
            self.minimum_usec,
            self.p50_usec,
            self.p95_usec,
            self.maximum_usec,
        )
        if any(value is None for value in values):
            raise FaultDatasetContractError(
                "non-empty metric summaries require all distribution values"
            )
        typed_values = tuple(value for value in values if value is not None)
        for index, value in enumerate(typed_values):
            _validate_nonnegative_int(value, field_name=f"distribution_value_{index}")
        if tuple(sorted(typed_values)) != typed_values:
            raise FaultDatasetContractError(
                "metric summary distribution values must be monotonic"
            )

    @classmethod
    def from_values(cls, values: Sequence[int | None]) -> FaultMetricSummary:
        """Build a deterministic summary without fabricating missing values."""

        observed: list[int] = []
        missing = 0
        for value in values:
            if value is None:
                missing += 1
                continue
            _validate_nonnegative_int(value, field_name="metric_value")
            observed.append(value)
        observed.sort()
        if not observed:
            return cls(0, missing, None, None, None, None)
        p50_index = (len(observed) - 1) // 2
        p95_index = max(0, math.ceil(0.95 * len(observed)) - 1)
        return cls(
            observed_count=len(observed),
            missing_count=missing,
            minimum_usec=observed[0],
            p50_usec=observed[p50_index],
            p95_usec=observed[p95_index],
            maximum_usec=observed[-1],
        )

    def to_dict(self) -> dict[str, int | None]:
        """Return a stable JSON-friendly summary."""

        return {
            "observed_count": self.observed_count,
            "missing_count": self.missing_count,
            "minimum_usec": self.minimum_usec,
            "p50_usec": self.p50_usec,
            "p95_usec": self.p95_usec,
            "maximum_usec": self.maximum_usec,
        }


@dataclass(frozen=True, slots=True)
class FaultDatasetRecord:
    """One labeled experiment record at a deterministic dataset ordinal."""

    ordinal: int
    run: FaultExperimentRun

    def __post_init__(self) -> None:
        _validate_positive_int(self.ordinal, field_name="ordinal")
        if not isinstance(self.run, FaultExperimentRun):
            raise FaultDatasetContractError("run must be a FaultExperimentRun")

    @property
    def fault_mode(self) -> FaultMode:
        """Return the authoritative fault mode from the injection outcome."""

        return self.run.outcome.plan.fault_mode

    def to_dict(self) -> dict[str, object]:
        """Return a stable labeled dataset record."""

        return {
            "schema_version": _SCHEMA_VERSION,
            "ordinal": self.ordinal,
            "experiment_id": self.run.outcome.plan.experiment_id,
            "scenario_id": self.run.outcome.plan.scenario_id,
            "target_unit": self.run.outcome.plan.target_unit,
            "fault_mode": self.fault_mode.value,
            "ground_truth_label": self.fault_mode.value,
            "label_source": "injected_ground_truth",
            "run": self.run.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class FaultDatasetModeReport:
    """Per-fault-mode coverage and visibility distributions."""

    fault_mode: FaultMode
    run_count: int
    core_coverage_count: int
    correlation_coverage_count: int
    exact_invocation_coverage_count: int
    total_fault_journal_entry_count: int
    total_fault_correlated_entry_count: int
    total_fault_exact_invocation_entry_count: int
    ground_truth_duration: FaultMetricSummary
    ground_truth_confirmation_lag: FaultMetricSummary
    service_fault_visibility_latency: FaultMetricSummary
    journal_fault_evidence_latency: FaultMetricSummary
    correlated_fault_evidence_latency: FaultMetricSummary
    exact_invocation_evidence_latency: FaultMetricSummary
    recovery_visibility_latency: FaultMetricSummary

    def __post_init__(self) -> None:
        if not isinstance(self.fault_mode, FaultMode):
            raise FaultDatasetContractError("fault_mode must be a FaultMode")
        for field_name, value in (
            ("run_count", self.run_count),
            ("core_coverage_count", self.core_coverage_count),
            ("correlation_coverage_count", self.correlation_coverage_count),
            ("exact_invocation_coverage_count", self.exact_invocation_coverage_count),
            (
                "total_fault_journal_entry_count",
                self.total_fault_journal_entry_count,
            ),
            (
                "total_fault_correlated_entry_count",
                self.total_fault_correlated_entry_count,
            ),
            (
                "total_fault_exact_invocation_entry_count",
                self.total_fault_exact_invocation_entry_count,
            ),
        ):
            _validate_nonnegative_int(value, field_name=field_name)
        for value in (
            self.core_coverage_count,
            self.correlation_coverage_count,
            self.exact_invocation_coverage_count,
        ):
            if value > self.run_count:
                raise FaultDatasetContractError(
                    "mode coverage counts cannot exceed run_count"
                )
        if (
            self.total_fault_correlated_entry_count
            > self.total_fault_journal_entry_count
        ):
            raise FaultDatasetContractError(
                "mode correlated evidence cannot exceed journal evidence"
            )
        if (
            self.total_fault_exact_invocation_entry_count
            > self.total_fault_correlated_entry_count
        ):
            raise FaultDatasetContractError(
                "mode exact invocation evidence cannot exceed correlated evidence"
            )
        for summary in self._summaries():
            if not isinstance(summary, FaultMetricSummary):
                raise FaultDatasetContractError(
                    "mode metric fields must be FaultMetricSummary values"
                )
            if summary.observed_count + summary.missing_count != self.run_count:
                raise FaultDatasetContractError(
                    "mode metric counts must equal run_count"
                )

    def _summaries(self) -> tuple[FaultMetricSummary, ...]:
        return (
            self.ground_truth_duration,
            self.ground_truth_confirmation_lag,
            self.service_fault_visibility_latency,
            self.journal_fault_evidence_latency,
            self.correlated_fault_evidence_latency,
            self.exact_invocation_evidence_latency,
            self.recovery_visibility_latency,
        )

    @property
    def core_coverage_rate(self) -> float | None:
        return _rate(self.core_coverage_count, self.run_count)

    @property
    def correlation_coverage_rate(self) -> float | None:
        return _rate(self.correlation_coverage_count, self.run_count)

    @property
    def exact_invocation_coverage_rate(self) -> float | None:
        return _rate(self.exact_invocation_coverage_count, self.run_count)

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly per-mode report."""

        return {
            "fault_mode": self.fault_mode.value,
            "run_count": self.run_count,
            "core_coverage_count": self.core_coverage_count,
            "core_coverage_rate": self.core_coverage_rate,
            "correlation_coverage_count": self.correlation_coverage_count,
            "correlation_coverage_rate": self.correlation_coverage_rate,
            "exact_invocation_coverage_count": self.exact_invocation_coverage_count,
            "exact_invocation_coverage_rate": self.exact_invocation_coverage_rate,
            "total_fault_journal_entry_count": self.total_fault_journal_entry_count,
            "total_fault_correlated_entry_count": (
                self.total_fault_correlated_entry_count
            ),
            "total_fault_exact_invocation_entry_count": (
                self.total_fault_exact_invocation_entry_count
            ),
            "metrics": _metric_summary_mapping(self),
        }


@dataclass(frozen=True, slots=True)
class FaultDatasetBenchmarkReport:
    """Aggregate coverage and visibility distributions across one dataset."""

    dataset_id: str
    total_run_count: int
    inactive_run_count: int
    failed_run_count: int
    core_coverage_count: int
    correlation_coverage_count: int
    exact_invocation_coverage_count: int
    total_fault_journal_entry_count: int
    total_fault_correlated_entry_count: int
    total_fault_exact_invocation_entry_count: int
    inactive: FaultDatasetModeReport
    failed: FaultDatasetModeReport
    ground_truth_duration: FaultMetricSummary
    ground_truth_confirmation_lag: FaultMetricSummary
    service_fault_visibility_latency: FaultMetricSummary
    journal_fault_evidence_latency: FaultMetricSummary
    correlated_fault_evidence_latency: FaultMetricSummary
    exact_invocation_evidence_latency: FaultMetricSummary
    recovery_visibility_latency: FaultMetricSummary

    def __post_init__(self) -> None:
        _validate_dataset_id(self.dataset_id)
        for field_name, value in (
            ("total_run_count", self.total_run_count),
            ("inactive_run_count", self.inactive_run_count),
            ("failed_run_count", self.failed_run_count),
            ("core_coverage_count", self.core_coverage_count),
            ("correlation_coverage_count", self.correlation_coverage_count),
            ("exact_invocation_coverage_count", self.exact_invocation_coverage_count),
            (
                "total_fault_journal_entry_count",
                self.total_fault_journal_entry_count,
            ),
            (
                "total_fault_correlated_entry_count",
                self.total_fault_correlated_entry_count,
            ),
            (
                "total_fault_exact_invocation_entry_count",
                self.total_fault_exact_invocation_entry_count,
            ),
        ):
            _validate_nonnegative_int(value, field_name=field_name)
        if self.inactive_run_count + self.failed_run_count != self.total_run_count:
            raise FaultDatasetContractError(
                "fault-mode counts must equal total_run_count"
            )
        for value in (
            self.core_coverage_count,
            self.correlation_coverage_count,
            self.exact_invocation_coverage_count,
        ):
            if value > self.total_run_count:
                raise FaultDatasetContractError(
                    "coverage counts cannot exceed total_run_count"
                )
        if (
            self.total_fault_correlated_entry_count
            > self.total_fault_journal_entry_count
        ):
            raise FaultDatasetContractError(
                "correlated evidence count cannot exceed journal evidence count"
            )
        if (
            self.total_fault_exact_invocation_entry_count
            > self.total_fault_correlated_entry_count
        ):
            raise FaultDatasetContractError(
                "exact invocation count cannot exceed correlated evidence count"
            )
        if not isinstance(self.inactive, FaultDatasetModeReport) or (
            self.inactive.fault_mode is not FaultMode.SERVICE_INACTIVE
        ):
            raise FaultDatasetContractError(
                "inactive report must describe service_inactive"
            )
        if not isinstance(self.failed, FaultDatasetModeReport) or (
            self.failed.fault_mode is not FaultMode.SERVICE_FAILED
        ):
            raise FaultDatasetContractError(
                "failed report must describe service_failed"
            )
        if self.inactive.run_count != self.inactive_run_count:
            raise FaultDatasetContractError("inactive report run_count mismatch")
        if self.failed.run_count != self.failed_run_count:
            raise FaultDatasetContractError("failed report run_count mismatch")
        for overall_value, slice_total in (
            (
                self.core_coverage_count,
                self.inactive.core_coverage_count + self.failed.core_coverage_count,
            ),
            (
                self.correlation_coverage_count,
                self.inactive.correlation_coverage_count
                + self.failed.correlation_coverage_count,
            ),
            (
                self.exact_invocation_coverage_count,
                self.inactive.exact_invocation_coverage_count
                + self.failed.exact_invocation_coverage_count,
            ),
        ):
            if overall_value != slice_total:
                raise FaultDatasetContractError(
                    "overall coverage counts must equal per-mode totals"
                )
        summaries = (
            self.ground_truth_duration,
            self.ground_truth_confirmation_lag,
            self.service_fault_visibility_latency,
            self.journal_fault_evidence_latency,
            self.correlated_fault_evidence_latency,
            self.exact_invocation_evidence_latency,
            self.recovery_visibility_latency,
        )
        for summary in summaries:
            if not isinstance(summary, FaultMetricSummary):
                raise FaultDatasetContractError(
                    "benchmark metric fields must be FaultMetricSummary values"
                )
            if summary.observed_count + summary.missing_count != self.total_run_count:
                raise FaultDatasetContractError(
                    "metric observed and missing counts must equal total_run_count"
                )

    @property
    def core_coverage_rate(self) -> float | None:
        return _rate(self.core_coverage_count, self.total_run_count)

    @property
    def correlation_coverage_rate(self) -> float | None:
        return _rate(self.correlation_coverage_count, self.total_run_count)

    @property
    def exact_invocation_coverage_rate(self) -> float | None:
        return _rate(self.exact_invocation_coverage_count, self.total_run_count)

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly aggregate benchmark report."""

        return {
            "schema_version": _SCHEMA_VERSION,
            "dataset_id": self.dataset_id,
            "total_run_count": self.total_run_count,
            "inactive_run_count": self.inactive_run_count,
            "failed_run_count": self.failed_run_count,
            "core_coverage_count": self.core_coverage_count,
            "core_coverage_rate": self.core_coverage_rate,
            "correlation_coverage_count": self.correlation_coverage_count,
            "correlation_coverage_rate": self.correlation_coverage_rate,
            "exact_invocation_coverage_count": self.exact_invocation_coverage_count,
            "exact_invocation_coverage_rate": self.exact_invocation_coverage_rate,
            "total_fault_journal_entry_count": self.total_fault_journal_entry_count,
            "total_fault_correlated_entry_count": (
                self.total_fault_correlated_entry_count
            ),
            "total_fault_exact_invocation_entry_count": (
                self.total_fault_exact_invocation_entry_count
            ),
            "by_fault_mode": {
                FaultMode.SERVICE_INACTIVE.value: self.inactive.to_dict(),
                FaultMode.SERVICE_FAILED.value: self.failed.to_dict(),
            },
            "metrics": {
                "ground_truth_duration_usec": self.ground_truth_duration.to_dict(),
                "ground_truth_confirmation_lag_usec": (
                    self.ground_truth_confirmation_lag.to_dict()
                ),
                "service_fault_visibility_latency_usec": (
                    self.service_fault_visibility_latency.to_dict()
                ),
                "journal_fault_evidence_latency_usec": (
                    self.journal_fault_evidence_latency.to_dict()
                ),
                "correlated_fault_evidence_latency_usec": (
                    self.correlated_fault_evidence_latency.to_dict()
                ),
                "exact_invocation_evidence_latency_usec": (
                    self.exact_invocation_evidence_latency.to_dict()
                ),
                "recovery_visibility_latency_usec": (
                    self.recovery_visibility_latency.to_dict()
                ),
            },
        }


@dataclass(frozen=True, slots=True)
class FaultExperimentDataset:
    """A complete balanced labeled dataset with aggregate benchmark report."""

    plan: FaultDatasetPlan
    records: tuple[FaultDatasetRecord, ...]
    report: FaultDatasetBenchmarkReport

    def __post_init__(self) -> None:
        if not isinstance(self.plan, FaultDatasetPlan):
            raise FaultDatasetContractError("plan must be a FaultDatasetPlan")
        if not isinstance(self.records, tuple):
            raise FaultDatasetContractError("records must be a tuple")
        if not isinstance(self.report, FaultDatasetBenchmarkReport):
            raise FaultDatasetContractError(
                "report must be a FaultDatasetBenchmarkReport"
            )
        if len(self.records) != self.plan.expected_run_count:
            raise FaultDatasetContractError(
                "record count must equal the dataset plan expected_run_count"
            )
        expected_cases = self.plan.case_sequence()
        experiment_ids: set[str] = set()
        for record, (ordinal, mode, scenario_id) in zip(
            self.records, expected_cases, strict=True
        ):
            if not isinstance(record, FaultDatasetRecord):
                raise FaultDatasetContractError(
                    "records must contain FaultDatasetRecord values"
                )
            if record.ordinal != ordinal:
                raise FaultDatasetContractError("record ordinals must be contiguous")
            plan = record.run.outcome.plan
            if record.fault_mode is not mode or plan.scenario_id != scenario_id:
                raise FaultDatasetContractError(
                    "record sequence must match the deterministic dataset plan"
                )
            if plan.target_unit != self.plan.target_unit:
                raise FaultDatasetContractError(
                    "every record target must match the dataset target_unit"
                )
            if plan.experiment_id in experiment_ids:
                raise FaultDatasetContractError(
                    "dataset experiment identifiers must be unique"
                )
            experiment_ids.add(plan.experiment_id)
        if self.report.dataset_id != self.plan.dataset_id:
            raise FaultDatasetContractError("report dataset_id must match the plan")
        expected_report = build_fault_dataset_report(self.plan.dataset_id, self.records)
        if self.report != expected_report:
            raise FaultDatasetContractError(
                "report must exactly match the dataset record aggregate"
            )

    def to_dict(self) -> dict[str, object]:
        """Return dataset metadata without duplicating full records."""

        return {
            "schema_version": _SCHEMA_VERSION,
            "plan": self.plan.to_dict(),
            "record_count": len(self.records),
            "report": self.report.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class FaultDatasetArtifact:
    """Canonical content-addressed representation of one dataset bundle."""

    dataset_id: str
    record_count: int
    records_jsonl: str
    report_json: str
    manifest_json: str
    records_sha256: str
    report_sha256: str

    def __post_init__(self) -> None:
        _validate_dataset_id(self.dataset_id)
        _validate_nonnegative_int(self.record_count, field_name="record_count")
        for field_name, value in (
            ("records_jsonl", self.records_jsonl),
            ("report_json", self.report_json),
            ("manifest_json", self.manifest_json),
        ):
            if not isinstance(value, str) or not value.endswith("\n"):
                raise FaultDatasetContractError(
                    f"{field_name} must be newline-terminated text"
                )
        for field_name, value in (
            ("records_sha256", self.records_sha256),
            ("report_sha256", self.report_sha256),
        ):
            _validate_sha256(value, field_name=field_name)
        if len(self.records_jsonl.encode("utf-8")) > _MAX_RECORDS_BYTES:
            raise FaultDatasetContractError(
                "records_jsonl exceeds the dataset size bound"
            )
        for field_name, value in (
            ("report_json", self.report_json),
            ("manifest_json", self.manifest_json),
        ):
            if len(value.encode("utf-8")) > _MAX_METADATA_BYTES:
                raise FaultDatasetContractError(
                    f"{field_name} exceeds the metadata size bound"
                )
        if _sha256_text(self.records_jsonl) != self.records_sha256:
            raise FaultDatasetContractError(
                "records_sha256 does not match records_jsonl"
            )
        if _sha256_text(self.report_json) != self.report_sha256:
            raise FaultDatasetContractError("report_sha256 does not match report_json")
        record_lines = self.records_jsonl.splitlines()
        if len(record_lines) != self.record_count:
            raise FaultDatasetContractError(
                "record_count must match the records_jsonl line count"
            )
        for line in record_lines:
            _parse_json_object(line, field_name="records_jsonl line")
        manifest = _parse_json_object(
            self.manifest_json,
            field_name="manifest_json",
        )
        _validate_artifact_manifest(self, manifest)

    def write_private_bundle(self, parent_directory: str | Path) -> Path:
        """Write one exclusive 0700/0600 dataset bundle under a trusted directory."""

        parent = _validate_private_parent_directory(parent_directory)
        destination = parent / self.dataset_id
        try:
            os.mkdir(destination, 0o700)
        except FileExistsError as exc:
            raise FaultDatasetWriteError("dataset destination already exists") from exc
        except OSError as exc:
            raise FaultDatasetWriteError(
                "failed to create dataset destination"
            ) from exc
        try:
            os.chmod(destination, 0o700)
            directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for filename, content in (
                    (_RECORDS_FILENAME, self.records_jsonl),
                    (_REPORT_FILENAME, self.report_json),
                    (_MANIFEST_FILENAME, self.manifest_json),
                ):
                    _write_exclusive_file(directory_fd, filename, content)
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            _remove_bundle_best_effort(destination)
            raise
        return destination


def build_fault_dataset_report(
    dataset_id: str,
    records: Sequence[FaultDatasetRecord],
) -> FaultDatasetBenchmarkReport:
    """Aggregate coverage and latency distributions without inventing measurements."""

    _validate_dataset_id(dataset_id)
    typed_records = tuple(records)
    for record in typed_records:
        if not isinstance(record, FaultDatasetRecord):
            raise FaultDatasetContractError(
                "records must contain FaultDatasetRecord values"
            )
    benchmarks = tuple(record.run.benchmark for record in typed_records)
    inactive_count = sum(
        record.fault_mode is FaultMode.SERVICE_INACTIVE for record in typed_records
    )
    failed_count = sum(
        record.fault_mode is FaultMode.SERVICE_FAILED for record in typed_records
    )
    return FaultDatasetBenchmarkReport(
        dataset_id=dataset_id,
        total_run_count=len(typed_records),
        inactive_run_count=inactive_count,
        failed_run_count=failed_count,
        core_coverage_count=sum(b.core_coverage_complete for b in benchmarks),
        correlation_coverage_count=sum(b.correlation_coverage for b in benchmarks),
        exact_invocation_coverage_count=sum(
            b.exact_invocation_coverage for b in benchmarks
        ),
        total_fault_journal_entry_count=sum(
            b.fault_journal_entry_count for b in benchmarks
        ),
        total_fault_correlated_entry_count=sum(
            b.fault_correlated_entry_count for b in benchmarks
        ),
        total_fault_exact_invocation_entry_count=sum(
            b.fault_exact_invocation_entry_count for b in benchmarks
        ),
        inactive=_build_mode_report(typed_records, FaultMode.SERVICE_INACTIVE),
        failed=_build_mode_report(typed_records, FaultMode.SERVICE_FAILED),
        ground_truth_duration=FaultMetricSummary.from_values(
            tuple(b.ground_truth_duration_usec for b in benchmarks)
        ),
        ground_truth_confirmation_lag=FaultMetricSummary.from_values(
            tuple(b.ground_truth_confirmation_lag_usec for b in benchmarks)
        ),
        service_fault_visibility_latency=FaultMetricSummary.from_values(
            tuple(b.service_fault_visibility_latency_usec for b in benchmarks)
        ),
        journal_fault_evidence_latency=FaultMetricSummary.from_values(
            tuple(b.journal_fault_evidence_latency_usec for b in benchmarks)
        ),
        correlated_fault_evidence_latency=FaultMetricSummary.from_values(
            tuple(b.correlated_fault_evidence_latency_usec for b in benchmarks)
        ),
        exact_invocation_evidence_latency=FaultMetricSummary.from_values(
            tuple(b.exact_invocation_evidence_latency_usec for b in benchmarks)
        ),
        recovery_visibility_latency=FaultMetricSummary.from_values(
            tuple(b.recovery_visibility_latency_usec for b in benchmarks)
        ),
    )


def _build_mode_report(
    records: Sequence[FaultDatasetRecord],
    mode: FaultMode,
) -> FaultDatasetModeReport:
    selected = tuple(record for record in records if record.fault_mode is mode)
    benchmarks = tuple(record.run.benchmark for record in selected)
    return FaultDatasetModeReport(
        fault_mode=mode,
        run_count=len(selected),
        core_coverage_count=sum(b.core_coverage_complete for b in benchmarks),
        correlation_coverage_count=sum(b.correlation_coverage for b in benchmarks),
        exact_invocation_coverage_count=sum(
            b.exact_invocation_coverage for b in benchmarks
        ),
        total_fault_journal_entry_count=sum(
            b.fault_journal_entry_count for b in benchmarks
        ),
        total_fault_correlated_entry_count=sum(
            b.fault_correlated_entry_count for b in benchmarks
        ),
        total_fault_exact_invocation_entry_count=sum(
            b.fault_exact_invocation_entry_count for b in benchmarks
        ),
        ground_truth_duration=FaultMetricSummary.from_values(
            tuple(b.ground_truth_duration_usec for b in benchmarks)
        ),
        ground_truth_confirmation_lag=FaultMetricSummary.from_values(
            tuple(b.ground_truth_confirmation_lag_usec for b in benchmarks)
        ),
        service_fault_visibility_latency=FaultMetricSummary.from_values(
            tuple(b.service_fault_visibility_latency_usec for b in benchmarks)
        ),
        journal_fault_evidence_latency=FaultMetricSummary.from_values(
            tuple(b.journal_fault_evidence_latency_usec for b in benchmarks)
        ),
        correlated_fault_evidence_latency=FaultMetricSummary.from_values(
            tuple(b.correlated_fault_evidence_latency_usec for b in benchmarks)
        ),
        exact_invocation_evidence_latency=FaultMetricSummary.from_values(
            tuple(b.exact_invocation_evidence_latency_usec for b in benchmarks)
        ),
        recovery_visibility_latency=FaultMetricSummary.from_values(
            tuple(b.recovery_visibility_latency_usec for b in benchmarks)
        ),
    )


def _metric_summary_mapping(
    report: FaultDatasetModeReport,
) -> dict[str, dict[str, int | None]]:
    return {
        "ground_truth_duration_usec": report.ground_truth_duration.to_dict(),
        "ground_truth_confirmation_lag_usec": (
            report.ground_truth_confirmation_lag.to_dict()
        ),
        "service_fault_visibility_latency_usec": (
            report.service_fault_visibility_latency.to_dict()
        ),
        "journal_fault_evidence_latency_usec": (
            report.journal_fault_evidence_latency.to_dict()
        ),
        "correlated_fault_evidence_latency_usec": (
            report.correlated_fault_evidence_latency.to_dict()
        ),
        "exact_invocation_evidence_latency_usec": (
            report.exact_invocation_evidence_latency.to_dict()
        ),
        "recovery_visibility_latency_usec": (
            report.recovery_visibility_latency.to_dict()
        ),
    }


def build_fault_dataset_artifact(
    dataset: FaultExperimentDataset,
) -> FaultDatasetArtifact:
    """Build canonical JSONL/report/manifest content with integrity digests."""

    if not isinstance(dataset, FaultExperimentDataset):
        raise FaultDatasetContractError("dataset must be a FaultExperimentDataset")
    records_jsonl = "".join(
        _canonical_json(
            {
                "dataset_id": dataset.plan.dataset_id,
                **record.to_dict(),
            }
        )
        for record in dataset.records
    )
    report_json = _canonical_json(dataset.report.to_dict())
    records_sha256 = _sha256_text(records_jsonl)
    report_sha256 = _sha256_text(report_json)
    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "dataset_id": dataset.plan.dataset_id,
        "created_at": dataset.plan.created_at.isoformat(),
        "target_unit": dataset.plan.target_unit,
        "record_count": len(dataset.records),
        "plan": dataset.plan.to_dict(),
        "files": {
            _RECORDS_FILENAME: {
                "sha256": records_sha256,
                "record_count": len(dataset.records),
            },
            _REPORT_FILENAME: {"sha256": report_sha256},
        },
    }
    return FaultDatasetArtifact(
        dataset_id=dataset.plan.dataset_id,
        record_count=len(dataset.records),
        records_jsonl=records_jsonl,
        report_json=report_json,
        manifest_json=_canonical_json(manifest),
        records_sha256=records_sha256,
        report_sha256=report_sha256,
    )


class SystemdFaultDatasetRunner:
    """Execute a balanced bounded dataset plan through the frozen 3D orchestrator."""

    def __init__(self, *, orchestrator: DatasetExperimentRunner | None = None) -> None:
        self._orchestrator: DatasetExperimentRunner = (
            SystemdFaultExperimentOrchestrator()
            if orchestrator is None
            else orchestrator
        )
        if not callable(getattr(self._orchestrator, "run", None)):
            raise TypeError("orchestrator.run must be callable")

    def run(
        self,
        plan: FaultDatasetPlan,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultExperimentDataset:
        """Run every planned case in deterministic order and aggregate the dataset."""

        if not isinstance(plan, FaultDatasetPlan):
            raise FaultDatasetContractError("plan must be a FaultDatasetPlan")
        if not isinstance(artifact, SystemdLabFixtureArtifact):
            raise FaultDatasetContractError(
                "artifact must be a SystemdLabFixtureArtifact"
            )
        if artifact.unit_name != plan.target_unit:
            raise FaultDatasetContractError(
                "dataset plan target_unit must match the fixture artifact"
            )
        records: list[FaultDatasetRecord] = []
        for ordinal, mode, scenario_id in plan.case_sequence():
            manifest = FaultExperimentManifest(
                FaultScenario(
                    scenario_id=scenario_id,
                    description=(
                        "Sentinel-X reproducible labeled fault dataset experiment."
                    ),
                    target_unit=plan.target_unit,
                    fault_mode=mode,
                    baseline_timeout_seconds=plan.baseline_timeout_seconds,
                    fault_timeout_seconds=plan.fault_timeout_seconds,
                    recovery_timeout_seconds=plan.recovery_timeout_seconds,
                    evidence_grace_seconds=plan.evidence_grace_seconds,
                )
            )
            run = self._orchestrator.run(manifest, artifact)
            records.append(FaultDatasetRecord(ordinal=ordinal, run=run))
        typed_records = tuple(records)
        report = build_fault_dataset_report(plan.dataset_id, typed_records)
        return FaultExperimentDataset(plan=plan, records=typed_records, report=report)


def _parse_json_object(value: str, *, field_name: str) -> dict[str, object]:
    try:
        parsed: object = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise FaultDatasetContractError(f"{field_name} must be valid JSON") from exc
    return _require_json_object(parsed, field_name=field_name)


def _require_json_object(value: object, *, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FaultDatasetContractError(f"{field_name} must contain a JSON object")
    result: dict[str, object] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise FaultDatasetContractError(
                f"{field_name} must contain only string object keys"
            )
        result[key] = nested_value
    return result


def _validate_artifact_manifest(
    artifact: FaultDatasetArtifact,
    manifest: dict[str, object],
) -> None:
    if manifest.get("schema_version") != _SCHEMA_VERSION:
        raise FaultDatasetContractError("manifest schema_version mismatch")
    if manifest.get("dataset_id") != artifact.dataset_id:
        raise FaultDatasetContractError("manifest dataset_id mismatch")
    if manifest.get("record_count") != artifact.record_count:
        raise FaultDatasetContractError("manifest record_count mismatch")
    files = _require_json_object(
        manifest.get("files"),
        field_name="manifest files",
    )
    records = _require_json_object(
        files.get(_RECORDS_FILENAME),
        field_name="manifest records metadata",
    )
    report = _require_json_object(
        files.get(_REPORT_FILENAME),
        field_name="manifest report metadata",
    )
    if records.get("sha256") != artifact.records_sha256:
        raise FaultDatasetContractError("manifest records digest mismatch")
    if records.get("record_count") != artifact.record_count:
        raise FaultDatasetContractError("manifest records count mismatch")
    if report.get("sha256") != artifact.report_sha256:
        raise FaultDatasetContractError("manifest report digest mismatch")


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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_dataset_id(value: str) -> str:
    if not isinstance(value, str) or _DATASET_ID_PATTERN.fullmatch(value) is None:
        raise FaultDatasetContractError(
            "dataset_id must be dataset- followed by 32 lowercase hex digits"
        )
    return value


def _validate_aware_datetime(value: datetime, *, field_name: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise FaultDatasetContractError(f"{field_name} must be timezone-aware")


def _validate_positive_seconds(value: float, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FaultDatasetContractError(f"{field_name} must be a positive number")
    normalized = float(value)
    if not 0.0 < normalized <= 300.0:
        raise FaultDatasetContractError(
            f"{field_name} must be greater than zero and at most 300 seconds"
        )
    return normalized


def _validate_nonnegative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FaultDatasetContractError(f"{field_name} must be a non-negative integer")


def _validate_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FaultDatasetContractError(f"{field_name} must be a positive integer")


def _validate_sha256(value: str, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise FaultDatasetContractError(f"{field_name} must be lowercase SHA-256 hex")


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _validate_private_parent_directory(value: str | Path) -> Path:
    path = Path(value)
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise FaultDatasetWriteError("dataset parent directory does not exist") from exc
    if path.is_symlink() or not path.is_dir():
        raise FaultDatasetWriteError(
            "dataset parent directory must be an existing non-symlink directory"
        )
    resolved = path.resolve()
    for forbidden in _FORBIDDEN_STORAGE_ROOTS:
        if resolved == forbidden or forbidden in resolved.parents:
            raise FaultDatasetWriteError(
                "dataset bundles must not be written into systemd unit trees"
            )
    if stat_result.st_mode & 0o022:
        raise FaultDatasetWriteError(
            "dataset parent directory must not be group- or world-writable"
        )
    return resolved


def _write_exclusive_file(directory_fd: int, filename: str, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        file_fd = os.open(filename, flags, 0o600, dir_fd=directory_fd)
    except OSError as exc:
        raise FaultDatasetWriteError(f"failed to create {filename}") from exc
    try:
        os.fchmod(file_fd, 0o600)
        data = content.encode("utf-8")
        written = 0
        while written < len(data):
            count = os.write(file_fd, data[written:])
            if count <= 0:
                raise FaultDatasetWriteError(f"short write while creating {filename}")
            written += count
        os.fsync(file_fd)
    finally:
        os.close(file_fd)


def _remove_bundle_best_effort(destination: Path) -> None:
    for filename in (_RECORDS_FILENAME, _REPORT_FILENAME, _MANIFEST_FILENAME):
        try:
            (destination / filename).unlink()
        except OSError:
            pass
    try:
        destination.rmdir()
    except OSError:
        pass
