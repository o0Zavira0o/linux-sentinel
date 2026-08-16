"""Bounded live execution for controlled Phase 5D.3 propagation pairs.

This module runs already-installed, byte-verified ``sentinel-x-lab-*`` pairs.
It never installs unit files, never invokes a shell, never escalates privileges,
and never upgrades one controlled observation into a causal, probabilistic, or
universal systemd claim.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from sentinel_x.dependency.discovery import (
    SystemctlDependencyUnitReader,
    SystemdDependencyDiscovery,
)
from sentinel_x.dependency.graph import DependencyGraphSnapshot, build_dependency_graph
from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.dependency.propagation import (
    DependencyPropagationCandidate,
    SystemdAssessmentEvidence,
    bind_systemd_assessment_evidence,
    build_dependency_propagation_candidates,
)
from sentinel_x.dependency.propagation_experiment import (
    ControlledPropagationExperimentRecord,
    SystemdPropagationPairArtifact,
    build_controlled_propagation_experiment_record,
    evaluate_controlled_ground_truth_coverage,
    verify_installed_systemd_propagation_pair,
)
from sentinel_x.detection.models import SystemdServiceHealthStatus
from sentinel_x.detection.systemd import SystemdServiceStateDetector
from sentinel_x.lab.injector import FaultInjectionOutcome, SystemdLabFaultInjector
from sentinel_x.lab.fixture import SystemdLabFixtureArtifact
from sentinel_x.lab.models import FaultExperimentManifest, FaultMode
from sentinel_x.systemd.boot import (
    SystemBootIdError,
    normalize_boot_id,
    read_current_boot_id,
)
from sentinel_x.systemd.observation import systemd_service_snapshot_to_event
from sentinel_x.systemd.reader import SystemctlServiceReader
from sentinel_x.systemd.models import SystemdServiceSnapshot

CONTROLLED_PROPAGATION_LIVE_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.controlled-propagation-live.v1"
)

_DEFAULT_SAMPLE_INTERVAL_SECONDS: Final[float] = 0.1
_MIN_SAMPLE_INTERVAL_SECONDS: Final[float] = 0.02
_MAX_SAMPLE_INTERVAL_SECONDS: Final[float] = 2.0
_DEFAULT_CAPTURE_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
_MIN_CAPTURE_JOIN_TIMEOUT_SECONDS: Final[float] = 0.1
_MAX_CAPTURE_JOIN_TIMEOUT_SECONDS: Final[float] = 30.0
_DEFAULT_MAX_SAMPLES: Final[int] = 4096
_MAX_SAMPLES: Final[int] = 65536
_DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[float] = 5.0
_MIN_COMMAND_TIMEOUT_SECONDS: Final[float] = 0.1
_MAX_COMMAND_TIMEOUT_SECONDS: Final[float] = 30.0
_DEFAULT_STATE_TIMEOUT_SECONDS: Final[float] = 10.0
_MIN_STATE_TIMEOUT_SECONDS: Final[float] = 0.1
_MAX_STATE_TIMEOUT_SECONDS: Final[float] = 60.0
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 0.1
_MIN_POLL_INTERVAL_SECONDS: Final[float] = 0.02
_MAX_POLL_INTERVAL_SECONDS: Final[float] = 2.0
_MAX_CAPTURE_BYTES: Final[int] = 65_536
_MAX_ERROR_TEXT_CHARS: Final[int] = 512
_COLLECTOR_NAME: Final[str] = "phase5d3b_dependent_sampling"


class ControlledPropagationLiveError(RuntimeError):
    """Base error for controlled Phase 5D.3B live execution."""


class ControlledPropagationLiveContractError(ControlledPropagationLiveError):
    """Raised when typed live-run inputs or outputs violate their contract."""


class ControlledPropagationLivePreconditionError(ControlledPropagationLiveError):
    """Raised before mutation when the controlled live preflight is not trustworthy."""


class ControlledPropagationLiveSamplingError(ControlledPropagationLiveError):
    """Raised when dependent assessment capture cannot remain trustworthy."""


class ControlledPropagationLiveCapacityError(ControlledPropagationLiveSamplingError):
    """Raised instead of silently dropping dependent assessment samples."""


class ControlledPropagationLiveRecoveryError(ControlledPropagationLiveError):
    """Raised when exact lab-pair recovery cannot be verified."""


@dataclass(frozen=True, slots=True)
class ControlledPropagationLivePolicy:
    """Caller-declared bounded execution and sampling policy."""

    max_sample_gap_usec: int
    sample_interval_seconds: float = _DEFAULT_SAMPLE_INTERVAL_SECONDS
    max_samples: int = _DEFAULT_MAX_SAMPLES
    capture_join_timeout_seconds: float = _DEFAULT_CAPTURE_JOIN_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        _validate_positive_int(
            self.max_sample_gap_usec, field_name="max_sample_gap_usec"
        )
        object.__setattr__(
            self,
            "sample_interval_seconds",
            _validate_seconds(
                self.sample_interval_seconds,
                field_name="sample_interval_seconds",
                minimum=_MIN_SAMPLE_INTERVAL_SECONDS,
                maximum=_MAX_SAMPLE_INTERVAL_SECONDS,
            ),
        )
        if (
            isinstance(self.max_samples, bool)
            or not isinstance(self.max_samples, int)
            or not 2 <= self.max_samples <= _MAX_SAMPLES
        ):
            raise ControlledPropagationLiveContractError(
                f"max_samples must be an integer between 2 and {_MAX_SAMPLES}"
            )
        object.__setattr__(
            self,
            "capture_join_timeout_seconds",
            _validate_seconds(
                self.capture_join_timeout_seconds,
                field_name="capture_join_timeout_seconds",
                minimum=_MIN_CAPTURE_JOIN_TIMEOUT_SECONDS,
                maximum=_MAX_CAPTURE_JOIN_TIMEOUT_SECONDS,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "max_sample_gap_usec": self.max_sample_gap_usec,
            "max_sample_gap_declared_by_caller": True,
            "sample_interval_seconds": self.sample_interval_seconds,
            "max_samples": self.max_samples,
            "capture_join_timeout_seconds": self.capture_join_timeout_seconds,
            "probabilistic_confidence_assigned": False,
        }


class AssessmentSampler(Protocol):
    """Return one typed assessment evidence sample for the dependent service."""

    def __call__(self) -> SystemdAssessmentEvidence:
        """Collect one sample."""


class CandidateProvider(Protocol):
    """Return one live graph candidate for the exact installed pair."""

    def __call__(
        self,
        artifact: SystemdPropagationPairArtifact,
        expected_boot_id: str,
    ) -> DependencyPropagationCandidate:
        """Discover and validate the controlled requirement candidate."""


class SamplerFactory(Protocol):
    """Build one typed sampler for an exact service identity."""

    def __call__(self, unit_name: str) -> AssessmentSampler:
        """Return a sampler bound to one service."""


class FaultInjector(Protocol):
    """Subset of the frozen Phase 3 injector used by live paired execution."""

    def execute(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultInjectionOutcome:
        """Execute and recover one controlled source fault."""


class PairLifecycle(Protocol):
    """Exact lab-pair lifecycle contract without installation responsibility."""

    def prepare(self, artifact: SystemdPropagationPairArtifact) -> None:
        """Make both already-installed pair members healthy before sampling."""

    def recover(self, artifact: SystemdPropagationPairArtifact) -> None:
        """Recover and verify both pair members after the controlled run."""


class PairVerifier(Protocol):
    """Verify exact installed bytes for both pair members."""

    def __call__(self, artifact: SystemdPropagationPairArtifact) -> None:
        """Verify the pair."""


@dataclass(frozen=True, slots=True)
class ControlledPropagationLiveRun:
    """One completed, boot-stable controlled live run."""

    pair_artifact: SystemdPropagationPairArtifact
    policy: ControlledPropagationLivePolicy
    candidate: DependencyPropagationCandidate
    injection_outcome: FaultInjectionOutcome
    dependent_assessments: tuple[SystemdAssessmentEvidence, ...]
    experiment_record: ControlledPropagationExperimentRecord
    boot_id_before: str
    boot_id_after: str
    post_recovery_verified: bool

    def __post_init__(self) -> None:
        if not isinstance(self.pair_artifact, SystemdPropagationPairArtifact):
            raise ControlledPropagationLiveContractError("pair_artifact must be typed")
        if not isinstance(self.policy, ControlledPropagationLivePolicy):
            raise ControlledPropagationLiveContractError("policy must be typed")
        if not isinstance(self.candidate, DependencyPropagationCandidate):
            raise ControlledPropagationLiveContractError("candidate must be typed")
        if not isinstance(self.injection_outcome, FaultInjectionOutcome):
            raise ControlledPropagationLiveContractError(
                "injection_outcome must be typed"
            )
        if not isinstance(self.dependent_assessments, tuple):
            raise ControlledPropagationLiveContractError(
                "dependent_assessments must be a tuple"
            )
        if not self.dependent_assessments:
            raise ControlledPropagationLiveContractError(
                "dependent_assessments must not be empty"
            )
        if len(self.dependent_assessments) > self.policy.max_samples:
            raise ControlledPropagationLiveContractError(
                "dependent assessment count exceeds policy capacity"
            )
        if not isinstance(
            self.experiment_record, ControlledPropagationExperimentRecord
        ):
            raise ControlledPropagationLiveContractError(
                "experiment_record must be typed"
            )
        before = _normalize_boot(self.boot_id_before, field_name="boot_id_before")
        after = _normalize_boot(self.boot_id_after, field_name="boot_id_after")
        if before != self.boot_id_before or after != self.boot_id_after:
            raise ControlledPropagationLiveContractError(
                "live-run boot identities must already be normalized"
            )
        if self.boot_id_before != self.boot_id_after:
            raise ControlledPropagationLiveContractError(
                "live run cannot compare evidence across boots"
            )
        if self.candidate.boot_id != self.boot_id_before:
            raise ControlledPropagationLiveContractError(
                "candidate boot must match live-run boot"
            )
        if self.candidate.dependency_unit != self.pair_artifact.source_unit:
            raise ControlledPropagationLiveContractError(
                "candidate dependency must match pair source"
            )
        if self.candidate.dependent_unit != self.pair_artifact.dependent_unit:
            raise ControlledPropagationLiveContractError(
                "candidate dependent must match pair dependent"
            )
        if (
            self.candidate.requirement_relation
            is not self.pair_artifact.spec.requirement_relation
        ):
            raise ControlledPropagationLiveContractError(
                "candidate relation must match pair relation"
            )
        evidence_ids: set[str] = set()
        previous_usec: int | None = None
        for evidence in self.dependent_assessments:
            if not isinstance(evidence, SystemdAssessmentEvidence):
                raise ControlledPropagationLiveContractError(
                    "dependent_assessments must contain typed evidence"
                )
            if evidence.evidence_id in evidence_ids:
                raise ControlledPropagationLiveContractError(
                    "dependent assessment evidence must be unique"
                )
            evidence_ids.add(evidence.evidence_id)
            if evidence.boot_id != self.boot_id_before:
                raise ControlledPropagationLiveContractError(
                    "dependent assessment boot must match live-run boot"
                )
            if evidence.canonical_unit != self.pair_artifact.dependent_unit:
                raise ControlledPropagationLiveContractError(
                    "dependent assessment unit must match pair dependent"
                )
            assessed_usec = evidence.assessment.assessed_monotonic_usec
            if previous_usec is not None and assessed_usec < previous_usec:
                raise ControlledPropagationLiveContractError(
                    "dependent assessments must be monotonic by sample time"
                )
            previous_usec = assessed_usec
        if self.experiment_record.candidate.candidate_id != self.candidate.candidate_id:
            raise ControlledPropagationLiveContractError(
                "experiment record candidate must match live candidate"
            )
        if self.experiment_record.injection_outcome != self.injection_outcome:
            raise ControlledPropagationLiveContractError(
                "experiment record outcome must match live outcome"
            )
        if self.experiment_record.pair_artifact.pair_id != self.pair_artifact.pair_id:
            raise ControlledPropagationLiveContractError(
                "experiment record pair must match live pair"
            )
        coverage = self.experiment_record.controlled_coverage_evidence
        if coverage.max_sample_gap_usec != self.policy.max_sample_gap_usec:
            raise ControlledPropagationLiveContractError(
                "coverage gap policy must match live policy"
            )
        if coverage.max_input_assessments != self.policy.max_samples:
            raise ControlledPropagationLiveContractError(
                "coverage capacity must match live policy"
            )
        if coverage.ground_truth_boot_id != self.boot_id_before:
            raise ControlledPropagationLiveContractError(
                "coverage ground-truth boot must match live boot"
            )
        if coverage.input_assessment_count != len(self.dependent_assessments):
            raise ControlledPropagationLiveContractError(
                "coverage input count must match live assessment count"
            )
        live_evidence_ids = {
            evidence.evidence_id for evidence in self.dependent_assessments
        }
        if any(
            evidence.evidence_id not in live_evidence_ids
            for evidence in coverage.in_window_assessments
        ):
            raise ControlledPropagationLiveContractError(
                "coverage references assessment evidence outside the live capture"
            )
        duration_usec = self.injection_outcome.ground_truth.duration_usec
        if duration_usec is None or coverage.analysis_window_usec != duration_usec:
            raise ControlledPropagationLiveContractError(
                "coverage analysis window must equal controlled fault duration"
            )
        if (
            type(self.post_recovery_verified) is not bool
            or not self.post_recovery_verified
        ):
            raise ControlledPropagationLiveContractError(
                "completed live run requires verified post-recovery state"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTROLLED_PROPAGATION_LIVE_SCHEMA_VERSION,
            "pair_id": self.pair_artifact.pair_id,
            "policy": self.policy.to_dict(),
            "candidate_id": self.candidate.candidate_id,
            "graph_version_id": self.candidate.graph_version_id,
            "topology_id": self.candidate.topology_id,
            "boot_id_before": self.boot_id_before,
            "boot_id_after": self.boot_id_after,
            "cross_boot_temporal_comparison_permitted": False,
            "dependent_assessment_evidence_ids": [
                evidence.evidence_id for evidence in self.dependent_assessments
            ],
            "dependent_assessment_count": len(self.dependent_assessments),
            "injection_experiment_id": self.injection_outcome.plan.experiment_id,
            "experiment_record_id": self.experiment_record.record_id,
            "evidence_class": self.experiment_record.evidence_class.value,
            "post_recovery_verified": self.post_recovery_verified,
            "installed_pair_verified_before_mutation": True,
            "installed_pair_reverified_after_recovery": True,
            "unit_installation_performed_by_runner": False,
            "privilege_escalation_performed_by_runner": False,
            "shell_invocation_performed_by_runner": False,
            "topology_temporal_applicability_claim": False,
            "causal_claim": False,
            "universal_systemd_behavior_claim": False,
            "root_cause_claim_assigned": False,
            "probabilistic_confidence_assigned": False,
        }


class SystemdDependentAssessmentSampler:
    """Linux-native typed assessment sampler for one controlled dependent."""

    def __init__(
        self,
        unit_name: str,
        *,
        reader: SystemctlServiceReader | None = None,
        detector: SystemdServiceStateDetector | None = None,
        boot_id_reader: Callable[[], str] = read_current_boot_id,
    ) -> None:
        if not isinstance(unit_name, str) or not unit_name.startswith(
            "sentinel-x-lab-"
        ):
            raise ControlledPropagationLiveContractError(
                "live sampler requires a sentinel-x-lab-* unit"
            )
        if not unit_name.endswith(".service"):
            raise ControlledPropagationLiveContractError(
                "live sampler requires a .service unit"
            )
        if not callable(boot_id_reader):
            raise TypeError("boot_id_reader must be callable")
        self._unit_name = unit_name
        self._reader = SystemctlServiceReader() if reader is None else reader
        self._detector = SystemdServiceStateDetector() if detector is None else detector
        self._boot_id_reader = boot_id_reader

    def __call__(self) -> SystemdAssessmentEvidence:
        snapshot = self._reader.read_service(self._unit_name)
        boot_id = _normalize_boot(self._boot_id_reader(), field_name="current boot ID")
        event = systemd_service_snapshot_to_event(
            snapshot,
            collector_name=_COLLECTOR_NAME,
            boot_id=boot_id,
        )
        assessment = self._detector.assess(event)
        return bind_systemd_assessment_evidence(assessment, event)


@dataclass(frozen=True, slots=True)
class _LifecycleCommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class _LifecycleRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> _LifecycleCommandResult: ...


class SystemdPropagationPairLifecycle:
    """Exact no-shell lifecycle operations for already-installed lab pair units."""

    def __init__(
        self,
        *,
        reader: SystemctlServiceReader | None = None,
        runner: _LifecycleRunner | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        systemctl_path: str | Path | None = None,
        command_timeout_seconds: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
        state_timeout_seconds: float = _DEFAULT_STATE_TIMEOUT_SECONDS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        pair_verifier: PairVerifier = verify_installed_systemd_propagation_pair,
    ) -> None:
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        if not callable(pair_verifier):
            raise TypeError("pair_verifier must be callable")
        if runner is not None and not callable(runner):
            raise TypeError("runner must be callable")
        if systemctl_path is None:
            resolved = shutil.which("systemctl")
            if resolved is None:
                raise ControlledPropagationLivePreconditionError(
                    "systemctl executable was not found in PATH"
                )
            normalized_path = resolved
        else:
            normalized_path = os.fspath(systemctl_path)
            if not normalized_path or "\x00" in normalized_path:
                raise ControlledPropagationLiveContractError(
                    "systemctl_path must be non-empty and contain no NUL"
                )
        self._reader = SystemctlServiceReader() if reader is None else reader
        self._runner = _default_lifecycle_runner if runner is None else runner
        self._sleeper = sleeper
        self._systemctl_path = normalized_path
        self._command_timeout_seconds = _validate_seconds(
            command_timeout_seconds,
            field_name="command_timeout_seconds",
            minimum=_MIN_COMMAND_TIMEOUT_SECONDS,
            maximum=_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        self._state_timeout_seconds = _validate_seconds(
            state_timeout_seconds,
            field_name="state_timeout_seconds",
            minimum=_MIN_STATE_TIMEOUT_SECONDS,
            maximum=_MAX_STATE_TIMEOUT_SECONDS,
        )
        self._poll_interval_seconds = _validate_seconds(
            poll_interval_seconds,
            field_name="poll_interval_seconds",
            minimum=_MIN_POLL_INTERVAL_SECONDS,
            maximum=_MAX_POLL_INTERVAL_SECONDS,
        )
        self._pair_verifier = pair_verifier

    def prepare(self, artifact: SystemdPropagationPairArtifact) -> None:
        self._pair_verifier(artifact)
        self._run("start", "--", artifact.dependent_unit)
        self._wait_pair_healthy(artifact)
        self._run("reset-failed", "--", artifact.source_unit, artifact.dependent_unit)
        self._pair_verifier(artifact)

    def recover(self, artifact: SystemdPropagationPairArtifact) -> None:
        try:
            self._pair_verifier(artifact)
            self._run("start", "--", artifact.source_unit)
            self._run("reset-failed", "--", artifact.source_unit)
            self._run("start", "--", artifact.dependent_unit)
            self._run("reset-failed", "--", artifact.dependent_unit)
            self._wait_pair_healthy(artifact)
            self._pair_verifier(artifact)
        except ControlledPropagationLiveError:
            raise
        except Exception as exc:
            raise ControlledPropagationLiveRecoveryError(
                "failed to recover controlled propagation pair"
            ) from exc

    def _run(self, *arguments: str) -> None:
        result = self._runner(
            (
                self._systemctl_path,
                "--system",
                "--no-ask-password",
                "--no-pager",
                *arguments,
            ),
            timeout_seconds=self._command_timeout_seconds,
        )
        stdout = _bounded_capture(result.stdout)
        stderr = _bounded_capture(result.stderr)
        if result.returncode != 0:
            detail = (stderr or stdout or "no command output")[:_MAX_ERROR_TEXT_CHARS]
            raise ControlledPropagationLiveRecoveryError(
                f"systemctl {' '.join(arguments)} failed with exit status "
                f"{result.returncode}: {detail}"
            )

    def _wait_pair_healthy(self, artifact: SystemdPropagationPairArtifact) -> None:
        deadline = time.monotonic() + self._state_timeout_seconds
        while True:
            source = self._reader.read_service(artifact.source_unit)
            dependent = self._reader.read_service(artifact.dependent_unit)
            if _snapshot_is_healthy(source) and _snapshot_is_healthy(dependent):
                return
            if time.monotonic() >= deadline:
                raise ControlledPropagationLiveRecoveryError(
                    "controlled pair did not reach healthy state before timeout"
                )
            self._sleeper(self._poll_interval_seconds)


class _DependentAssessmentCapture:
    def __init__(
        self,
        *,
        sampler: AssessmentSampler,
        dependent_unit: str,
        expected_boot_id: str,
        sample_interval_seconds: float,
        max_samples: int,
        join_timeout_seconds: float,
    ) -> None:
        self._sampler = sampler
        self._dependent_unit = dependent_unit
        self._expected_boot_id = expected_boot_id
        self._sample_interval_seconds = sample_interval_seconds
        self._max_samples = max_samples
        self._join_timeout_seconds = join_timeout_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._samples: list[SystemdAssessmentEvidence] = []
        self._failure: BaseException | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> SystemdAssessmentEvidence:
        if self._thread is not None:
            raise ControlledPropagationLiveSamplingError("capture already started")
        first = self._sample_once()
        if first.assessment.status is not SystemdServiceHealthStatus.HEALTHY:
            raise ControlledPropagationLivePreconditionError(
                "dependent must be HEALTHY at live capture start"
            )
        thread = threading.Thread(
            target=self._run,
            name="sentinel-x-phase5d3b-dependent-capture",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return first

    def finish(self) -> tuple[SystemdAssessmentEvidence, ...]:
        self._stop.set()
        thread = self._thread
        if thread is None:
            raise ControlledPropagationLiveSamplingError("capture was not started")
        thread.join(self._join_timeout_seconds)
        if thread.is_alive():
            raise ControlledPropagationLiveSamplingError(
                "dependent capture thread did not stop within join timeout"
            )
        if self._failure is not None:
            raise ControlledPropagationLiveSamplingError(
                "dependent assessment capture failed"
            ) from self._failure
        with self._lock:
            return tuple(self._samples)

    def abort(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(self._join_timeout_seconds)
            if thread.is_alive():
                raise ControlledPropagationLiveSamplingError(
                    "dependent capture thread did not stop during abort"
                )

    def _run(self) -> None:
        try:
            while not self._stop.wait(self._sample_interval_seconds):
                self._sample_once()
        except BaseException as exc:
            with self._lock:
                if self._failure is None:
                    self._failure = exc
            self._stop.set()

    def _sample_once(self) -> SystemdAssessmentEvidence:
        evidence = self._sampler()
        if not isinstance(evidence, SystemdAssessmentEvidence):
            raise ControlledPropagationLiveSamplingError(
                "sampler must return SystemdAssessmentEvidence"
            )
        if evidence.boot_id != self._expected_boot_id:
            raise ControlledPropagationLiveSamplingError(
                "dependent sample boot changed during live capture"
            )
        if evidence.canonical_unit != self._dependent_unit:
            raise ControlledPropagationLiveSamplingError(
                "dependent sampler returned an unexpected unit"
            )
        with self._lock:
            if len(self._samples) >= self._max_samples:
                raise ControlledPropagationLiveCapacityError(
                    "dependent live sampling capacity exceeded"
                )
            if self._samples:
                previous = self._samples[-1].assessment.assessed_monotonic_usec
                current = evidence.assessment.assessed_monotonic_usec
                if current < previous:
                    raise ControlledPropagationLiveSamplingError(
                        "dependent sampling monotonic time regressed"
                    )
            if any(
                existing.evidence_id == evidence.evidence_id
                for existing in self._samples
            ):
                raise ControlledPropagationLiveSamplingError(
                    "dependent sampler produced duplicate evidence identity"
                )
            self._samples.append(evidence)
        return evidence


class ControlledPropagationLiveRunner:
    """Execute one controlled source deactivation while sampling the dependent."""

    def __init__(
        self,
        *,
        pair_verifier: PairVerifier = verify_installed_systemd_propagation_pair,
        boot_id_reader: Callable[[], str] = read_current_boot_id,
        candidate_provider: CandidateProvider | None = None,
        sampler_factory: SamplerFactory | None = None,
        injector: FaultInjector | None = None,
        lifecycle: PairLifecycle | None = None,
    ) -> None:
        if not callable(pair_verifier):
            raise TypeError("pair_verifier must be callable")
        if not callable(boot_id_reader):
            raise TypeError("boot_id_reader must be callable")
        if candidate_provider is not None and not callable(candidate_provider):
            raise TypeError("candidate_provider must be callable")
        if sampler_factory is not None and not callable(sampler_factory):
            raise TypeError("sampler_factory must be callable")
        self._pair_verifier = pair_verifier
        self._boot_id_reader = boot_id_reader
        self._candidate_provider = (
            discover_live_pair_candidate
            if candidate_provider is None
            else candidate_provider
        )
        self._sampler_factory = (
            _default_sampler_factory if sampler_factory is None else sampler_factory
        )
        self._injector = SystemdLabFaultInjector() if injector is None else injector
        if not callable(getattr(self._injector, "execute", None)):
            raise TypeError("injector must provide a callable execute")
        self._lifecycle = (
            SystemdPropagationPairLifecycle() if lifecycle is None else lifecycle
        )
        if not callable(getattr(self._lifecycle, "prepare", None)) or not callable(
            getattr(self._lifecycle, "recover", None)
        ):
            raise TypeError("lifecycle must provide callable prepare and recover")

    def run(
        self,
        artifact: SystemdPropagationPairArtifact,
        manifest: FaultExperimentManifest,
        policy: ControlledPropagationLivePolicy,
    ) -> ControlledPropagationLiveRun:
        _validate_live_inputs(artifact, manifest, policy)
        _verify_live_pair(self._pair_verifier, artifact)
        boot_before = _normalize_boot(self._boot_id_reader(), field_name="boot before")
        try:
            self._lifecycle.prepare(artifact)
        except BaseException as prepare_error:
            try:
                self._lifecycle.recover(artifact)
            except BaseException as prepare_recovery_error:
                raise ControlledPropagationLiveRecoveryError(
                    "pair recovery failed after live preparation error"
                ) from prepare_recovery_error
            raise prepare_error
        _verify_live_pair(self._pair_verifier, artifact)
        _require_same_boot(boot_before, self._boot_id_reader(), stage="after prepare")

        candidate = self._candidate_provider(artifact, boot_before)
        _validate_live_candidate(candidate, artifact, boot_before)
        _verify_live_pair(self._pair_verifier, artifact)
        _require_same_boot(boot_before, self._boot_id_reader(), stage="before fault")

        capture = _DependentAssessmentCapture(
            sampler=self._sampler_factory(artifact.dependent_unit),
            dependent_unit=artifact.dependent_unit,
            expected_boot_id=boot_before,
            sample_interval_seconds=policy.sample_interval_seconds,
            max_samples=policy.max_samples,
            join_timeout_seconds=policy.capture_join_timeout_seconds,
        )
        capture.start()

        outcome: FaultInjectionOutcome | None = None
        assessments: tuple[SystemdAssessmentEvidence, ...] | None = None
        primary_error: BaseException | None = None
        abort_error: BaseException | None = None
        try:
            outcome = self._injector.execute(manifest, artifact.source_artifact)
            _validate_outcome_matches_manifest(outcome, manifest, artifact)
            assessments = capture.finish()
        except BaseException as exc:
            primary_error = exc
            try:
                capture.abort()
            except BaseException as stop_error:
                abort_error = stop_error

        recovery_error: BaseException | None = None
        try:
            self._lifecycle.recover(artifact)
        except BaseException as exc:
            recovery_error = exc

        if recovery_error is not None:
            if primary_error is not None:
                raise ControlledPropagationLiveRecoveryError(
                    "pair recovery failed after a live-run error"
                ) from recovery_error
            raise ControlledPropagationLiveRecoveryError(
                "pair recovery failed after controlled injection"
            ) from recovery_error
        if abort_error is not None:
            raise ControlledPropagationLiveSamplingError(
                "dependent capture could not stop after live-run failure"
            ) from abort_error
        if primary_error is not None:
            raise primary_error
        if outcome is None or assessments is None:
            raise ControlledPropagationLiveContractError(
                "live run completed without injection outcome or assessments"
            )

        _verify_live_pair(self._pair_verifier, artifact)
        boot_after = _normalize_boot(self._boot_id_reader(), field_name="boot after")
        if boot_after != boot_before:
            raise ControlledPropagationLiveContractError(
                "boot changed during controlled live run"
            )
        duration_usec = outcome.ground_truth.duration_usec
        if duration_usec is None or duration_usec <= 0:
            raise ControlledPropagationLiveContractError(
                "closed controlled ground truth must have positive duration"
            )
        coverage = evaluate_controlled_ground_truth_coverage(
            candidate,
            outcome.ground_truth,
            boot_before,
            assessments,
            analysis_window_usec=duration_usec,
            max_sample_gap_usec=policy.max_sample_gap_usec,
            max_input_assessments=policy.max_samples,
        )
        record = build_controlled_propagation_experiment_record(
            artifact,
            outcome,
            candidate,
            coverage,
        )
        return ControlledPropagationLiveRun(
            pair_artifact=artifact,
            policy=policy,
            candidate=candidate,
            injection_outcome=outcome,
            dependent_assessments=assessments,
            experiment_record=record,
            boot_id_before=boot_before,
            boot_id_after=boot_after,
            post_recovery_verified=True,
        )


def discover_live_pair_candidate(
    artifact: SystemdPropagationPairArtifact,
    expected_boot_id: str,
) -> DependencyPropagationCandidate:
    """Read only the dependent root and validate the exact manager pair relation."""

    if not isinstance(artifact, SystemdPropagationPairArtifact):
        raise ControlledPropagationLiveContractError("artifact must be typed")
    normalized_boot = _normalize_boot(expected_boot_id, field_name="expected_boot_id")
    if normalized_boot != expected_boot_id:
        raise ControlledPropagationLiveContractError(
            "expected_boot_id must already be normalized"
        )
    report = SystemdDependencyDiscovery(
        reader=SystemctlDependencyUnitReader(),
        max_depth=0,
        max_units=1,
        boot_id_reader=lambda: expected_boot_id,
    ).discover(artifact.dependent_unit)
    graph = build_dependency_graph(report)
    return _candidate_from_graph(graph, artifact, expected_boot_id)


def _candidate_from_graph(
    graph: DependencyGraphSnapshot,
    artifact: SystemdPropagationPairArtifact,
    expected_boot_id: str,
) -> DependencyPropagationCandidate:
    if graph.boot_id != expected_boot_id:
        raise ControlledPropagationLivePreconditionError(
            "live dependency graph boot does not match preflight boot"
        )
    pair_edges = tuple(
        edge
        for edge in graph.edges
        if edge.subject_identity == artifact.dependent_unit
        and edge.object_identity == artifact.source_unit
    )
    requirement_edges = tuple(
        edge
        for edge in pair_edges
        if edge.relation in {DependencyRelation.REQUIRES, DependencyRelation.WANTS}
    )
    if len(requirement_edges) != 1:
        raise ControlledPropagationLivePreconditionError(
            "live manager must expose exactly one controlled pair requirement edge"
        )
    if requirement_edges[0].relation is not artifact.spec.requirement_relation:
        raise ControlledPropagationLivePreconditionError(
            "live manager requirement relation does not match installed pair artifact"
        )
    if not any(edge.relation is DependencyRelation.AFTER for edge in pair_edges):
        raise ControlledPropagationLivePreconditionError(
            "live manager is missing the controlled pair After ordering edge"
        )
    candidates = tuple(
        candidate
        for candidate in build_dependency_propagation_candidates(graph)
        if candidate.dependency_unit == artifact.source_unit
        and candidate.dependent_unit == artifact.dependent_unit
    )
    if len(candidates) != 1:
        raise ControlledPropagationLivePreconditionError(
            "live graph must yield exactly one controlled pair propagation candidate"
        )
    return candidates[0]


def _verify_live_pair(
    verifier: PairVerifier,
    artifact: SystemdPropagationPairArtifact,
) -> None:
    try:
        verifier(artifact)
    except ControlledPropagationLiveError:
        raise
    except Exception as exc:
        raise ControlledPropagationLivePreconditionError(
            "installed controlled pair verification failed"
        ) from exc


def _validate_outcome_matches_manifest(
    outcome: FaultInjectionOutcome,
    manifest: FaultExperimentManifest,
    artifact: SystemdPropagationPairArtifact,
) -> None:
    if not isinstance(outcome, FaultInjectionOutcome):
        raise ControlledPropagationLiveContractError(
            "injector must return FaultInjectionOutcome"
        )
    if outcome.plan.experiment_id != manifest.experiment_id:
        raise ControlledPropagationLiveContractError(
            "injection outcome experiment does not match manifest"
        )
    if outcome.plan.scenario_id != manifest.scenario.scenario_id:
        raise ControlledPropagationLiveContractError(
            "injection outcome scenario does not match manifest"
        )
    if outcome.plan.target_unit != artifact.source_unit:
        raise ControlledPropagationLiveContractError(
            "injection outcome target does not match pair source"
        )
    if outcome.plan.fault_mode is not FaultMode.SERVICE_INACTIVE:
        raise ControlledPropagationLiveContractError(
            "injection outcome fault mode must be SERVICE_INACTIVE"
        )


def _validate_live_inputs(
    artifact: SystemdPropagationPairArtifact,
    manifest: FaultExperimentManifest,
    policy: ControlledPropagationLivePolicy,
) -> None:
    if not isinstance(artifact, SystemdPropagationPairArtifact):
        raise ControlledPropagationLiveContractError("artifact must be typed")
    if not isinstance(manifest, FaultExperimentManifest):
        raise ControlledPropagationLiveContractError("manifest must be typed")
    if not isinstance(policy, ControlledPropagationLivePolicy):
        raise ControlledPropagationLiveContractError("policy must be typed")
    if manifest.scenario.target_unit != artifact.source_unit:
        raise ControlledPropagationLiveContractError(
            "manifest must target the controlled pair source"
        )
    if manifest.scenario.fault_mode is not FaultMode.SERVICE_INACTIVE:
        raise ControlledPropagationLiveContractError(
            "Phase 5D.3B requires explicit SERVICE_INACTIVE source deactivation"
        )


def _validate_live_candidate(
    candidate: DependencyPropagationCandidate,
    artifact: SystemdPropagationPairArtifact,
    expected_boot_id: str,
) -> None:
    if not isinstance(candidate, DependencyPropagationCandidate):
        raise ControlledPropagationLiveContractError(
            "candidate provider returned wrong type"
        )
    if candidate.boot_id != expected_boot_id:
        raise ControlledPropagationLivePreconditionError("candidate boot mismatch")
    if candidate.dependency_unit != artifact.source_unit:
        raise ControlledPropagationLivePreconditionError("candidate source mismatch")
    if candidate.dependent_unit != artifact.dependent_unit:
        raise ControlledPropagationLivePreconditionError("candidate dependent mismatch")
    if candidate.requirement_relation is not artifact.spec.requirement_relation:
        raise ControlledPropagationLivePreconditionError("candidate relation mismatch")


def _default_sampler_factory(unit_name: str) -> AssessmentSampler:
    return SystemdDependentAssessmentSampler(unit_name)


def _snapshot_is_healthy(snapshot: SystemdServiceSnapshot) -> bool:
    return (
        snapshot.load_state == "loaded"
        and snapshot.active_state == "active"
        and snapshot.sub_state == "running"
        and snapshot.main_pid is not None
        and snapshot.main_pid > 0
    )


def _default_lifecycle_runner(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
) -> _LifecycleCommandResult:
    try:
        completed = subprocess.run(
            tuple(argv),
            shell=False,
            check=False,
            capture_output=True,
            timeout=timeout_seconds,
            env={
                "PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin"),
                "LC_ALL": "C",
                "LANG": "C",
            },
        )
    except subprocess.TimeoutExpired as exc:
        raise ControlledPropagationLiveRecoveryError(
            f"systemctl lifecycle command exceeded {timeout_seconds:.3f} seconds"
        ) from exc
    except OSError as exc:
        raise ControlledPropagationLiveRecoveryError(
            f"systemctl lifecycle command failed: {exc}"
        ) from exc
    return _LifecycleCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _bounded_capture(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise ControlledPropagationLiveContractError("command capture must be bytes")
    if len(value) > _MAX_CAPTURE_BYTES:
        raise ControlledPropagationLiveRecoveryError(
            "systemctl command capture is too large"
        )
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ControlledPropagationLiveRecoveryError(
            "systemctl command capture is not valid UTF-8"
        ) from exc


def _require_same_boot(expected: str, observed: object, *, stage: str) -> None:
    normalized = _normalize_boot(observed, field_name=f"boot {stage}")
    if normalized != expected:
        raise ControlledPropagationLivePreconditionError(
            f"boot changed {stage}; controlled temporal evidence is invalid"
        )


def _normalize_boot(value: object, *, field_name: str) -> str:
    try:
        return normalize_boot_id(value, field_name=field_name)
    except SystemBootIdError as exc:
        raise ControlledPropagationLiveContractError(str(exc)) from exc


def _validate_positive_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ControlledPropagationLiveContractError(
            f"{field_name} must be a positive integer"
        )


def _validate_seconds(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControlledPropagationLiveContractError(f"{field_name} must be a number")
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise ControlledPropagationLiveContractError(
            f"{field_name} must be between {minimum:g} and {maximum:g} seconds"
        )
    return normalized
