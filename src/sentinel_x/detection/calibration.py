"""Empirical calibration evidence for repeated systemd detector benchmarks."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from sentinel_x.detection.benchmarking import (
    DetectionBenchmarkScopeReport,
    DetectionIntegerMetricSummary,
    RepeatedDetectionBenchmark,
    RepeatedDetectionBenchmarkRecord,
)
from sentinel_x.lab import FaultMode
from sentinel_x.systemd.models import validate_service_unit_name

DETECTION_CALIBRATION_SCHEMA_VERSION: Final[int] = 1
DETECTION_CALIBRATION_VERSION: Final[str] = (
    "sentinel-x.systemd-detection-calibration.v1"
)
_CALIBRATION_ID_DOMAIN: Final[str] = "sentinel-x.systemd-calibration-id.v1"
_CALIBRATION_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^cal-[0-9a-f]{32}$")
_BENCHMARK_FILENAME: Final[str] = "benchmark.json"
_CALIBRATION_FILENAME: Final[str] = "calibration.json"
_MANIFEST_FILENAME: Final[str] = "manifest.json"
_MAX_BENCHMARK_BYTES: Final[int] = 2_000_000
_MAX_METADATA_BYTES: Final[int] = 262_144
_FORBIDDEN_STORAGE_ROOTS: Final[tuple[Path, ...]] = (
    Path("/run/systemd/system"),
    Path("/etc/systemd/system"),
    Path("/usr/lib/systemd/system"),
)


class DetectionCalibrationError(RuntimeError):
    """Base error for empirical detector calibration artifacts."""


class DetectionCalibrationContractError(DetectionCalibrationError):
    """Raised when calibration evidence violates its typed contract."""


class DetectionCalibrationWriteError(DetectionCalibrationError):
    """Raised when a private calibration artifact cannot be persisted safely."""


class CalibrationEvidenceStatus(str, Enum):
    """Evidence interpretation without probabilistic confidence."""

    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INCOMPLETE = "incomplete"


class CalibrationMetricAvailability(str, Enum):
    """Availability of a descriptive metric across benchmark runs."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CalibrationClaimKind(str, Enum):
    """Controlled-lab claims derived directly from benchmark observations."""

    RUN_DETECTION_COVERAGE = "run_detection_coverage"
    GROUND_TRUTH_FAULT_STATE_CLASSIFICATION = "ground_truth_fault_state_classification"
    HEALTHY_CONTROL_CLEANLINESS = "healthy_control_cleanliness"
    GROUND_TRUTH_OTHER_ANOMALY_ABSENCE = "ground_truth_other_anomaly_absence"


class CalibrationMetricKind(str, Enum):
    """Descriptive benchmark metrics retained without threshold invention."""

    DETECTION_VISIBILITY_LATENCY_USEC = "detection_visibility_latency_usec"
    DETECTION_TRANSITION_OFFSET_USEC = "detection_transition_offset_usec"
    GROUND_TRUTH_CONFIRMATION_OFFSET_USEC = "ground_truth_confirmation_offset_usec"
    RECOVERY_DETECTION_LATENCY_USEC = "recovery_detection_latency_usec"


class CalibrationRateKind(str, Enum):
    """Descriptive uncertainty rates retained without pass/fail thresholds."""

    EVALUATION_WINDOW_UNASSESSED = "evaluation_window_unassessed"
    GROUND_TRUTH_UNASSESSED = "ground_truth_unassessed"


class CalibrationScopeKind(str, Enum):
    """Overall and per-fault-mode calibration scopes."""

    OVERALL = "overall"
    SERVICE_INACTIVE = "service_inactive"
    SERVICE_FAILED = "service_failed"


@dataclass(frozen=True, slots=True)
class DetectionCalibrationClaim:
    """One empirical claim with explicit counterexamples and missing runs."""

    kind: CalibrationClaimKind
    observation_count: int
    counterexample_count: int
    incomplete_run_count: int
    status: CalibrationEvidenceStatus

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CalibrationClaimKind):
            raise DetectionCalibrationContractError(
                "kind must be a CalibrationClaimKind"
            )
        for field_name in (
            "observation_count",
            "counterexample_count",
            "incomplete_run_count",
        ):
            _validate_nonnegative_int(getattr(self, field_name), field_name=field_name)
        if self.counterexample_count > self.observation_count:
            raise DetectionCalibrationContractError(
                "counterexample_count cannot exceed observation_count"
            )
        if not isinstance(self.status, CalibrationEvidenceStatus):
            raise DetectionCalibrationContractError(
                "status must be a CalibrationEvidenceStatus"
            )
        expected = _claim_status(
            self.observation_count,
            self.counterexample_count,
            self.incomplete_run_count,
        )
        if self.status is not expected:
            raise DetectionCalibrationContractError(
                "claim status does not match observed evidence"
            )

    @classmethod
    def from_counts(
        cls,
        kind: CalibrationClaimKind,
        *,
        observation_count: int,
        counterexample_count: int,
        incomplete_run_count: int,
    ) -> DetectionCalibrationClaim:
        """Build a claim without assigning confidence or probabilities."""

        return cls(
            kind=kind,
            observation_count=observation_count,
            counterexample_count=counterexample_count,
            incomplete_run_count=incomplete_run_count,
            status=_claim_status(
                observation_count,
                counterexample_count,
                incomplete_run_count,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        """Return stable claim evidence."""

        return {
            "kind": self.kind.value,
            "observation_count": self.observation_count,
            "counterexample_count": self.counterexample_count,
            "incomplete_run_count": self.incomplete_run_count,
            "status": self.status.value,
        }


@dataclass(frozen=True, slots=True)
class DetectionCalibrationRateEvidence:
    """One descriptive rate with an explicit numerator and denominator."""

    kind: CalibrationRateKind
    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CalibrationRateKind):
            raise DetectionCalibrationContractError(
                "kind must be a CalibrationRateKind"
            )
        _validate_nonnegative_int(self.numerator, field_name="numerator")
        _validate_nonnegative_int(self.denominator, field_name="denominator")
        if self.numerator > self.denominator:
            raise DetectionCalibrationContractError(
                "rate numerator cannot exceed denominator"
            )

    @property
    def rate(self) -> float | None:
        """Return the observed fraction, or None when no denominator exists."""

        if self.denominator == 0:
            return None
        return self.numerator / self.denominator

    def to_dict(self) -> dict[str, object]:
        """Return explicit count-based rate evidence."""

        return {
            "kind": self.kind.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "rate": self.rate,
        }


@dataclass(frozen=True, slots=True)
class DetectionCalibrationMetricEvidence:
    """One observed metric distribution with explicit run-level missingness."""

    kind: CalibrationMetricKind
    run_count: int
    summary: DetectionIntegerMetricSummary
    availability: CalibrationMetricAvailability

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CalibrationMetricKind):
            raise DetectionCalibrationContractError(
                "kind must be a CalibrationMetricKind"
            )
        _validate_nonnegative_int(self.run_count, field_name="run_count")
        if not isinstance(self.summary, DetectionIntegerMetricSummary):
            raise DetectionCalibrationContractError(
                "summary must be a DetectionIntegerMetricSummary"
            )
        if self.summary.observed_count + self.summary.missing_count != self.run_count:
            raise DetectionCalibrationContractError(
                "metric observed and missing counts must cover every run"
            )
        if not isinstance(self.availability, CalibrationMetricAvailability):
            raise DetectionCalibrationContractError(
                "availability must be a CalibrationMetricAvailability"
            )
        expected = _metric_availability(self.summary)
        if self.availability is not expected:
            raise DetectionCalibrationContractError(
                "metric availability does not match summary missingness"
            )
        if self.kind in {
            CalibrationMetricKind.DETECTION_VISIBILITY_LATENCY_USEC,
            CalibrationMetricKind.RECOVERY_DETECTION_LATENCY_USEC,
        }:
            if self.summary.minimum is not None and self.summary.minimum < 0:
                raise DetectionCalibrationContractError(
                    "latency calibration metrics must be non-negative"
                )

    @classmethod
    def from_summary(
        cls,
        kind: CalibrationMetricKind,
        *,
        run_count: int,
        summary: DetectionIntegerMetricSummary,
    ) -> DetectionCalibrationMetricEvidence:
        """Wrap one frozen benchmark summary without changing its distribution."""

        return cls(
            kind=kind,
            run_count=run_count,
            summary=summary,
            availability=_metric_availability(summary),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the exact descriptive distribution and availability."""

        return {
            "kind": self.kind.value,
            "run_count": self.run_count,
            "availability": self.availability.value,
            "summary": self.summary.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DetectionCalibrationScope:
    """Controlled-lab calibration evidence for one benchmark scope."""

    kind: CalibrationScopeKind
    run_count: int
    detection_coverage: DetectionCalibrationClaim
    ground_truth_fault_state_classification: DetectionCalibrationClaim
    healthy_control_cleanliness: DetectionCalibrationClaim
    ground_truth_other_anomaly_absence: DetectionCalibrationClaim
    evaluation_window_unassessed: DetectionCalibrationRateEvidence
    ground_truth_unassessed: DetectionCalibrationRateEvidence
    detection_visibility_latency_usec: DetectionCalibrationMetricEvidence
    detection_transition_offset_usec: DetectionCalibrationMetricEvidence
    ground_truth_confirmation_offset_usec: DetectionCalibrationMetricEvidence
    recovery_detection_latency_usec: DetectionCalibrationMetricEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CalibrationScopeKind):
            raise DetectionCalibrationContractError(
                "kind must be a CalibrationScopeKind"
            )
        _validate_positive_int(self.run_count, field_name="run_count")
        expected_claim_kinds = (
            (
                self.detection_coverage,
                CalibrationClaimKind.RUN_DETECTION_COVERAGE,
            ),
            (
                self.ground_truth_fault_state_classification,
                CalibrationClaimKind.GROUND_TRUTH_FAULT_STATE_CLASSIFICATION,
            ),
            (
                self.healthy_control_cleanliness,
                CalibrationClaimKind.HEALTHY_CONTROL_CLEANLINESS,
            ),
            (
                self.ground_truth_other_anomaly_absence,
                CalibrationClaimKind.GROUND_TRUTH_OTHER_ANOMALY_ABSENCE,
            ),
        )
        for claim, expected_claim_kind in expected_claim_kinds:
            if not isinstance(claim, DetectionCalibrationClaim):
                raise DetectionCalibrationContractError(
                    "scope claims must be DetectionCalibrationClaim values"
                )
            if claim.kind is not expected_claim_kind:
                raise DetectionCalibrationContractError(
                    "scope claim kind does not match its field"
                )
            if claim.incomplete_run_count > self.run_count:
                raise DetectionCalibrationContractError(
                    "claim incomplete runs cannot exceed scope run_count"
                )
        expected_rate_kinds = (
            (
                self.evaluation_window_unassessed,
                CalibrationRateKind.EVALUATION_WINDOW_UNASSESSED,
            ),
            (
                self.ground_truth_unassessed,
                CalibrationRateKind.GROUND_TRUTH_UNASSESSED,
            ),
        )
        for rate_evidence, expected_rate_kind in expected_rate_kinds:
            if not isinstance(rate_evidence, DetectionCalibrationRateEvidence):
                raise DetectionCalibrationContractError(
                    "scope rates must be DetectionCalibrationRateEvidence values"
                )
            if rate_evidence.kind is not expected_rate_kind:
                raise DetectionCalibrationContractError(
                    "scope rate kind does not match its field"
                )
        expected_metric_kinds = (
            (
                self.detection_visibility_latency_usec,
                CalibrationMetricKind.DETECTION_VISIBILITY_LATENCY_USEC,
            ),
            (
                self.detection_transition_offset_usec,
                CalibrationMetricKind.DETECTION_TRANSITION_OFFSET_USEC,
            ),
            (
                self.ground_truth_confirmation_offset_usec,
                CalibrationMetricKind.GROUND_TRUTH_CONFIRMATION_OFFSET_USEC,
            ),
            (
                self.recovery_detection_latency_usec,
                CalibrationMetricKind.RECOVERY_DETECTION_LATENCY_USEC,
            ),
        )
        for metric_evidence, expected_metric_kind in expected_metric_kinds:
            if not isinstance(metric_evidence, DetectionCalibrationMetricEvidence):
                raise DetectionCalibrationContractError(
                    "scope metrics must be DetectionCalibrationMetricEvidence values"
                )
            if metric_evidence.kind is not expected_metric_kind:
                raise DetectionCalibrationContractError(
                    "scope metric kind does not match its field"
                )
            if metric_evidence.run_count != self.run_count:
                raise DetectionCalibrationContractError(
                    "scope metric run_count must match scope run_count"
                )

    @property
    def core_claims_supported(self) -> bool:
        """Return whether all non-threshold core lab claims lack counterexamples."""

        return all(
            claim.status is CalibrationEvidenceStatus.SUPPORTED
            for claim in (
                self.detection_coverage,
                self.ground_truth_fault_state_classification,
                self.healthy_control_cleanliness,
                self.ground_truth_other_anomaly_absence,
            )
        )

    def to_dict(self) -> dict[str, object]:
        """Return stable scope calibration evidence."""

        return {
            "kind": self.kind.value,
            "run_count": self.run_count,
            "core_claims_supported": self.core_claims_supported,
            "claims": {
                "detection_coverage": self.detection_coverage.to_dict(),
                "ground_truth_fault_state_classification": (
                    self.ground_truth_fault_state_classification.to_dict()
                ),
                "healthy_control_cleanliness": (
                    self.healthy_control_cleanliness.to_dict()
                ),
                "ground_truth_other_anomaly_absence": (
                    self.ground_truth_other_anomaly_absence.to_dict()
                ),
            },
            "rates": {
                "evaluation_window_unassessed": (
                    self.evaluation_window_unassessed.to_dict()
                ),
                "ground_truth_unassessed": self.ground_truth_unassessed.to_dict(),
            },
            "metrics": {
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
            },
        }


@dataclass(frozen=True, slots=True)
class DetectionCalibrationReport:
    """Deterministic empirical calibration report for one repeated benchmark."""

    calibration_id: str
    benchmark_sha256: str
    target_unit: str
    benchmark_run_count: int
    overall: DetectionCalibrationScope
    service_inactive: DetectionCalibrationScope
    service_failed: DetectionCalibrationScope

    def __post_init__(self) -> None:
        _validate_calibration_id(self.calibration_id)
        _validate_sha256(self.benchmark_sha256, field_name="benchmark_sha256")
        if self.calibration_id != _calibration_id(self.benchmark_sha256):
            raise DetectionCalibrationContractError(
                "calibration_id must be derived from benchmark_sha256"
            )
        try:
            normalized_unit = validate_service_unit_name(
                self.target_unit,
                field_name="target_unit",
            )
        except (TypeError, ValueError) as exc:
            raise DetectionCalibrationContractError(str(exc)) from exc
        if normalized_unit != self.target_unit:
            raise DetectionCalibrationContractError(
                "target_unit must already be canonical"
            )
        _validate_positive_int(
            self.benchmark_run_count,
            field_name="benchmark_run_count",
        )
        expected_scopes = (
            (self.overall, CalibrationScopeKind.OVERALL),
            (self.service_inactive, CalibrationScopeKind.SERVICE_INACTIVE),
            (self.service_failed, CalibrationScopeKind.SERVICE_FAILED),
        )
        for scope, expected_kind in expected_scopes:
            if not isinstance(scope, DetectionCalibrationScope):
                raise DetectionCalibrationContractError(
                    "report scopes must be DetectionCalibrationScope values"
                )
            if scope.kind is not expected_kind:
                raise DetectionCalibrationContractError(
                    "report scope kind does not match its field"
                )
        if self.overall.run_count != self.benchmark_run_count:
            raise DetectionCalibrationContractError(
                "overall run_count must match benchmark_run_count"
            )
        if (
            self.service_inactive.run_count + self.service_failed.run_count
            != self.overall.run_count
        ):
            raise DetectionCalibrationContractError(
                "per-mode calibration run counts must sum to overall"
            )

    @property
    def controlled_lab_core_claims_supported(self) -> bool:
        """Return whether core claims are supported in overall and both modes."""

        return (
            self.overall.core_claims_supported
            and self.service_inactive.core_claims_supported
            and self.service_failed.core_claims_supported
        )

    def to_dict(self) -> dict[str, object]:
        """Return a canonical calibration report without probabilistic claims."""

        return {
            "schema_version": DETECTION_CALIBRATION_SCHEMA_VERSION,
            "calibration_version": DETECTION_CALIBRATION_VERSION,
            "calibration_id": self.calibration_id,
            "benchmark_sha256": self.benchmark_sha256,
            "target_unit": self.target_unit,
            "benchmark_run_count": self.benchmark_run_count,
            "evidence_scope": "controlled_lab_empirical_observations",
            "probabilistic_confidence_assigned": False,
            "operational_thresholds_derived": False,
            "controlled_lab_core_claims_supported": (
                self.controlled_lab_core_claims_supported
            ),
            "overall": self.overall.to_dict(),
            "service_inactive": self.service_inactive.to_dict(),
            "service_failed": self.service_failed.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DetectionCalibrationArtifact:
    """Content-addressed benchmark and calibration bundle."""

    calibration_id: str
    benchmark_json: str
    calibration_json: str
    manifest_json: str
    benchmark_sha256: str
    calibration_sha256: str

    def __post_init__(self) -> None:
        _validate_calibration_id(self.calibration_id)
        _validate_sha256(self.benchmark_sha256, field_name="benchmark_sha256")
        _validate_sha256(self.calibration_sha256, field_name="calibration_sha256")
        if self.calibration_id != _calibration_id(self.benchmark_sha256):
            raise DetectionCalibrationContractError(
                "artifact calibration_id must be derived from benchmark_sha256"
            )
        for field_name, value in (
            ("benchmark_json", self.benchmark_json),
            ("calibration_json", self.calibration_json),
            ("manifest_json", self.manifest_json),
        ):
            if not isinstance(value, str) or not value.endswith("\n"):
                raise DetectionCalibrationContractError(
                    f"{field_name} must be newline-terminated text"
                )
        if len(self.benchmark_json.encode("utf-8")) > _MAX_BENCHMARK_BYTES:
            raise DetectionCalibrationContractError(
                "benchmark_json exceeds the calibration bundle size bound"
            )
        for field_name, value in (
            ("calibration_json", self.calibration_json),
            ("manifest_json", self.manifest_json),
        ):
            if len(value.encode("utf-8")) > _MAX_METADATA_BYTES:
                raise DetectionCalibrationContractError(
                    f"{field_name} exceeds the calibration metadata size bound"
                )
        if _sha256_text(self.benchmark_json) != self.benchmark_sha256:
            raise DetectionCalibrationContractError(
                "benchmark_sha256 does not match benchmark_json"
            )
        if _sha256_text(self.calibration_json) != self.calibration_sha256:
            raise DetectionCalibrationContractError(
                "calibration_sha256 does not match calibration_json"
            )
        benchmark = _parse_json_object(
            self.benchmark_json,
            field_name="benchmark_json",
        )
        calibration = _parse_json_object(
            self.calibration_json,
            field_name="calibration_json",
        )
        manifest = _parse_json_object(
            self.manifest_json,
            field_name="manifest_json",
        )
        if calibration.get("schema_version") != DETECTION_CALIBRATION_SCHEMA_VERSION:
            raise DetectionCalibrationContractError(
                "calibration_json schema_version does not match"
            )
        if calibration.get("calibration_version") != DETECTION_CALIBRATION_VERSION:
            raise DetectionCalibrationContractError(
                "calibration_json calibration_version does not match"
            )
        if calibration.get("calibration_id") != self.calibration_id:
            raise DetectionCalibrationContractError(
                "calibration_json identity does not match artifact"
            )
        if calibration.get("benchmark_sha256") != self.benchmark_sha256:
            raise DetectionCalibrationContractError(
                "calibration_json benchmark digest does not match artifact"
            )
        plan = benchmark.get("plan")
        if not isinstance(plan, dict):
            raise DetectionCalibrationContractError(
                "benchmark_json must contain a benchmark plan"
            )
        if plan.get("target_unit") != calibration.get("target_unit"):
            raise DetectionCalibrationContractError(
                "benchmark and calibration target units must match"
            )
        records = benchmark.get("records")
        if not isinstance(records, list):
            raise DetectionCalibrationContractError(
                "benchmark_json must contain benchmark records"
            )
        if calibration.get("benchmark_run_count") != len(records):
            raise DetectionCalibrationContractError(
                "calibration benchmark_run_count must match benchmark records"
            )
        _validate_artifact_manifest(self, manifest)

    def to_dict(self) -> dict[str, object]:
        """Return bounded artifact metadata without duplicating file bodies."""

        return {
            "schema_version": DETECTION_CALIBRATION_SCHEMA_VERSION,
            "calibration_version": DETECTION_CALIBRATION_VERSION,
            "calibration_id": self.calibration_id,
            "benchmark_sha256": self.benchmark_sha256,
            "calibration_sha256": self.calibration_sha256,
            "benchmark_bytes": len(self.benchmark_json.encode("utf-8")),
            "calibration_bytes": len(self.calibration_json.encode("utf-8")),
        }

    def write_private_bundle(self, parent_directory: str | Path) -> Path:
        """Write one exclusive 0700/0600 calibration bundle."""

        parent = _validate_private_parent_directory(parent_directory)
        destination = parent / self.calibration_id
        try:
            os.mkdir(destination, 0o700)
        except FileExistsError as exc:
            raise DetectionCalibrationWriteError(
                "calibration destination already exists"
            ) from exc
        except OSError as exc:
            raise DetectionCalibrationWriteError(
                "failed to create calibration destination"
            ) from exc
        try:
            os.chmod(destination, 0o700)
            directory_fd = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
            try:
                for filename, content in (
                    (_BENCHMARK_FILENAME, self.benchmark_json),
                    (_CALIBRATION_FILENAME, self.calibration_json),
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


def build_detection_calibration(
    benchmark: RepeatedDetectionBenchmark,
) -> DetectionCalibrationReport:
    """Build deterministic calibration evidence from one frozen repeated benchmark."""

    if not isinstance(benchmark, RepeatedDetectionBenchmark):
        raise DetectionCalibrationContractError(
            "benchmark must be a RepeatedDetectionBenchmark"
        )
    benchmark_json = _canonical_json(benchmark.to_dict())
    benchmark_sha256 = _sha256_text(benchmark_json)
    calibration_id = _calibration_id(benchmark_sha256)
    inactive_records = tuple(
        record
        for record in benchmark.records
        if record.fault_mode is FaultMode.SERVICE_INACTIVE
    )
    failed_records = tuple(
        record
        for record in benchmark.records
        if record.fault_mode is FaultMode.SERVICE_FAILED
    )
    return DetectionCalibrationReport(
        calibration_id=calibration_id,
        benchmark_sha256=benchmark_sha256,
        target_unit=benchmark.plan.target_unit,
        benchmark_run_count=len(benchmark.records),
        overall=_build_scope(
            CalibrationScopeKind.OVERALL,
            benchmark.records,
            benchmark.report.overall,
        ),
        service_inactive=_build_scope(
            CalibrationScopeKind.SERVICE_INACTIVE,
            inactive_records,
            benchmark.report.service_inactive,
        ),
        service_failed=_build_scope(
            CalibrationScopeKind.SERVICE_FAILED,
            failed_records,
            benchmark.report.service_failed,
        ),
    )


def build_detection_calibration_artifact(
    benchmark: RepeatedDetectionBenchmark,
) -> DetectionCalibrationArtifact:
    """Build canonical benchmark/calibration JSON and an integrity manifest."""

    report = build_detection_calibration(benchmark)
    benchmark_json = _canonical_json(benchmark.to_dict())
    calibration_json = _canonical_json(report.to_dict())
    benchmark_sha256 = _sha256_text(benchmark_json)
    calibration_sha256 = _sha256_text(calibration_json)
    manifest = {
        "schema_version": DETECTION_CALIBRATION_SCHEMA_VERSION,
        "calibration_version": DETECTION_CALIBRATION_VERSION,
        "calibration_id": report.calibration_id,
        "files": {
            _BENCHMARK_FILENAME: {
                "bytes": len(benchmark_json.encode("utf-8")),
                "sha256": benchmark_sha256,
            },
            _CALIBRATION_FILENAME: {
                "bytes": len(calibration_json.encode("utf-8")),
                "sha256": calibration_sha256,
            },
        },
    }
    return DetectionCalibrationArtifact(
        calibration_id=report.calibration_id,
        benchmark_json=benchmark_json,
        calibration_json=calibration_json,
        manifest_json=_canonical_json(manifest),
        benchmark_sha256=benchmark_sha256,
        calibration_sha256=calibration_sha256,
    )


def _build_scope(
    kind: CalibrationScopeKind,
    records: tuple[RepeatedDetectionBenchmarkRecord, ...],
    report: DetectionBenchmarkScopeReport,
) -> DetectionCalibrationScope:
    if not records:
        raise DetectionCalibrationContractError(
            "calibration scopes require at least one benchmark record"
        )
    if len(records) != report.run_count:
        raise DetectionCalibrationContractError(
            "scope record count must match its benchmark report"
        )
    classification_incomplete_runs = sum(
        record.ground_truth_fault_state_sample_count == 0 for record in records
    )
    return DetectionCalibrationScope(
        kind=kind,
        run_count=report.run_count,
        detection_coverage=DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.RUN_DETECTION_COVERAGE,
            observation_count=report.run_count,
            counterexample_count=(
                report.run_count - report.detection_covered_run_count
            ),
            incomplete_run_count=0,
        ),
        ground_truth_fault_state_classification=DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.GROUND_TRUTH_FAULT_STATE_CLASSIFICATION,
            observation_count=report.total_ground_truth_fault_state_sample_count,
            counterexample_count=(
                report.total_ground_truth_missed_fault_state_sample_count
            ),
            incomplete_run_count=classification_incomplete_runs,
        ),
        healthy_control_cleanliness=DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.HEALTHY_CONTROL_CLEANLINESS,
            observation_count=report.total_healthy_control_sample_count,
            counterexample_count=(report.total_healthy_control_false_positive_count),
            incomplete_run_count=(
                report.run_count - report.healthy_control_complete_run_count
            ),
        ),
        ground_truth_other_anomaly_absence=DetectionCalibrationClaim.from_counts(
            CalibrationClaimKind.GROUND_TRUTH_OTHER_ANOMALY_ABSENCE,
            observation_count=report.total_ground_truth_active_sample_count,
            counterexample_count=report.total_ground_truth_other_anomaly_count,
            incomplete_run_count=(
                report.run_count - report.ground_truth_active_observed_run_count
            ),
        ),
        evaluation_window_unassessed=DetectionCalibrationRateEvidence(
            CalibrationRateKind.EVALUATION_WINDOW_UNASSESSED,
            report.total_fault_window_unassessed_count,
            report.total_fault_window_sample_count,
        ),
        ground_truth_unassessed=DetectionCalibrationRateEvidence(
            CalibrationRateKind.GROUND_TRUTH_UNASSESSED,
            report.total_ground_truth_unassessed_count,
            report.total_ground_truth_active_sample_count,
        ),
        detection_visibility_latency_usec=(
            DetectionCalibrationMetricEvidence.from_summary(
                CalibrationMetricKind.DETECTION_VISIBILITY_LATENCY_USEC,
                run_count=report.run_count,
                summary=report.detection_visibility_latency_usec,
            )
        ),
        detection_transition_offset_usec=(
            DetectionCalibrationMetricEvidence.from_summary(
                CalibrationMetricKind.DETECTION_TRANSITION_OFFSET_USEC,
                run_count=report.run_count,
                summary=report.detection_transition_offset_usec,
            )
        ),
        ground_truth_confirmation_offset_usec=(
            DetectionCalibrationMetricEvidence.from_summary(
                CalibrationMetricKind.GROUND_TRUTH_CONFIRMATION_OFFSET_USEC,
                run_count=report.run_count,
                summary=report.ground_truth_confirmation_offset_usec,
            )
        ),
        recovery_detection_latency_usec=(
            DetectionCalibrationMetricEvidence.from_summary(
                CalibrationMetricKind.RECOVERY_DETECTION_LATENCY_USEC,
                run_count=report.run_count,
                summary=report.recovery_detection_latency_usec,
            )
        ),
    )


def _claim_status(
    observation_count: int,
    counterexample_count: int,
    incomplete_run_count: int,
) -> CalibrationEvidenceStatus:
    for field_name, value in (
        ("observation_count", observation_count),
        ("counterexample_count", counterexample_count),
        ("incomplete_run_count", incomplete_run_count),
    ):
        _validate_nonnegative_int(value, field_name=field_name)
    if counterexample_count > observation_count:
        raise DetectionCalibrationContractError(
            "counterexample_count cannot exceed observation_count"
        )
    if counterexample_count > 0:
        return CalibrationEvidenceStatus.CONTRADICTED
    if observation_count == 0 or incomplete_run_count > 0:
        return CalibrationEvidenceStatus.INCOMPLETE
    return CalibrationEvidenceStatus.SUPPORTED


def _metric_availability(
    summary: DetectionIntegerMetricSummary,
) -> CalibrationMetricAvailability:
    if not isinstance(summary, DetectionIntegerMetricSummary):
        raise DetectionCalibrationContractError(
            "summary must be a DetectionIntegerMetricSummary"
        )
    if summary.observed_count == 0:
        return CalibrationMetricAvailability.UNAVAILABLE
    if summary.missing_count == 0:
        return CalibrationMetricAvailability.COMPLETE
    return CalibrationMetricAvailability.PARTIAL


def _calibration_id(benchmark_sha256: str) -> str:
    _validate_sha256(benchmark_sha256, field_name="benchmark_sha256")
    material = f"{_CALIBRATION_ID_DOMAIN}\0{benchmark_sha256}".encode("ascii")
    return f"cal-{hashlib.sha256(material).hexdigest()[:32]}"


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


def _parse_json_object(value: str, *, field_name: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DetectionCalibrationContractError(
            f"{field_name} must contain valid JSON"
        ) from exc
    if not isinstance(parsed, dict):
        raise DetectionCalibrationContractError(
            f"{field_name} must contain a JSON object"
        )
    return parsed


def _validate_artifact_manifest(
    artifact: DetectionCalibrationArtifact,
    manifest: dict[str, object],
) -> None:
    if manifest.get("schema_version") != DETECTION_CALIBRATION_SCHEMA_VERSION:
        raise DetectionCalibrationContractError(
            "manifest schema_version does not match calibration schema"
        )
    if manifest.get("calibration_version") != DETECTION_CALIBRATION_VERSION:
        raise DetectionCalibrationContractError(
            "manifest calibration_version does not match"
        )
    if manifest.get("calibration_id") != artifact.calibration_id:
        raise DetectionCalibrationContractError(
            "manifest calibration_id does not match artifact"
        )
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise DetectionCalibrationContractError("manifest files must be an object")
    expected = {
        _BENCHMARK_FILENAME: (
            artifact.benchmark_sha256,
            len(artifact.benchmark_json.encode("utf-8")),
        ),
        _CALIBRATION_FILENAME: (
            artifact.calibration_sha256,
            len(artifact.calibration_json.encode("utf-8")),
        ),
    }
    if set(files) != set(expected):
        raise DetectionCalibrationContractError(
            "manifest files must contain exactly benchmark and calibration metadata"
        )
    for filename, (expected_sha, expected_bytes) in expected.items():
        metadata = files.get(filename)
        if not isinstance(metadata, dict):
            raise DetectionCalibrationContractError(
                f"manifest metadata for {filename} must be an object"
            )
        if metadata.get("sha256") != expected_sha:
            raise DetectionCalibrationContractError(
                f"manifest digest for {filename} does not match artifact"
            )
        if metadata.get("bytes") != expected_bytes:
            raise DetectionCalibrationContractError(
                f"manifest byte count for {filename} does not match artifact"
            )


def _validate_calibration_id(value: str) -> None:
    if not isinstance(value, str) or _CALIBRATION_ID_PATTERN.fullmatch(value) is None:
        raise DetectionCalibrationContractError(
            "calibration_id must have shape cal-<32 lowercase hex>"
        )


def _validate_sha256(value: str, *, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise DetectionCalibrationContractError(
            f"{field_name} must be lowercase SHA-256 hex"
        )


def _validate_nonnegative_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DetectionCalibrationContractError(
            f"{field_name} must be a non-negative integer"
        )


def _validate_positive_int(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DetectionCalibrationContractError(
            f"{field_name} must be a positive integer"
        )


def _validate_private_parent_directory(value: str | Path) -> Path:
    path = Path(value)
    try:
        stat_result = path.lstat()
    except OSError as exc:
        raise DetectionCalibrationWriteError(
            "calibration parent directory does not exist"
        ) from exc
    if path.is_symlink() or not path.is_dir():
        raise DetectionCalibrationWriteError(
            "calibration parent must be an existing non-symlink directory"
        )
    resolved = path.resolve()
    for forbidden in _FORBIDDEN_STORAGE_ROOTS:
        if resolved == forbidden or forbidden in resolved.parents:
            raise DetectionCalibrationWriteError(
                "calibration bundles must not be written into systemd unit trees"
            )
    if stat_result.st_mode & 0o022:
        raise DetectionCalibrationWriteError(
            "calibration parent must not be group- or world-writable"
        )
    return resolved


def _write_exclusive_file(directory_fd: int, filename: str, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        file_fd = os.open(filename, flags, 0o600, dir_fd=directory_fd)
    except OSError as exc:
        raise DetectionCalibrationWriteError(f"failed to create {filename}") from exc
    try:
        os.fchmod(file_fd, 0o600)
        data = content.encode("utf-8")
        written = 0
        while written < len(data):
            count = os.write(file_fd, data[written:])
            if count <= 0:
                raise DetectionCalibrationWriteError(
                    f"short write while creating {filename}"
                )
            written += count
        os.fsync(file_fd)
    finally:
        os.close(file_fd)


def _remove_bundle_best_effort(destination: Path) -> None:
    for filename in (
        _BENCHMARK_FILENAME,
        _CALIBRATION_FILENAME,
        _MANIFEST_FILENAME,
    ):
        try:
            (destination / filename).unlink()
        except OSError:
            pass
    try:
        destination.rmdir()
    except OSError:
        pass
