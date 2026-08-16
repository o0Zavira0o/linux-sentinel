"""Protocol-bound live execution for controlled propagation experiments.

Phase 5E.4 binds one immutable Phase 5E.3 protocol capture to one exact
Phase 5D.3B live execution through a standard, explicitly profiled Sentinel-X
backend.  The coordinator captures and validates the protocol before invoking
the live runner, binds the capture to a stable boot and privilege context, and
preserves an attempt record even when the live runner fails.

The contract is intentionally narrower than replication or causal inference.
It establishes scoped ordering and binding evidence only for mutations initiated
by the bound live runner invocation.  It does not claim that the host environment is fully
captured, that no external actor mutated system state, or that one execution
establishes propagation, treatment effect, statistical significance, or RCA.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Final

from sentinel_x.dependency.discovery import (
    SystemctlDependencyUnitReader,
    SystemdDependencyDiscovery,
)
from sentinel_x.dependency.graph import build_dependency_graph
from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.dependency.propagation import (
    DependencyPropagationCandidate,
    build_dependency_propagation_candidates,
)
from sentinel_x.dependency.propagation_experiment import (
    SystemdPropagationPairArtifact,
    verify_installed_systemd_propagation_pair,
)
from sentinel_x.dependency.propagation_live import (
    CONTROLLED_PROPAGATION_LIVE_SCHEMA_VERSION,
    ControlledPropagationLivePolicy,
    ControlledPropagationLiveRun,
    ControlledPropagationLiveRunner,
    ControlledPropagationLivePreconditionError,
    SystemdDependentAssessmentSampler,
    SystemdPropagationPairLifecycle,
)
from sentinel_x.dependency.protocol import (
    CONTROLLED_PROPAGATION_PROTOCOL_SCHEMA_VERSION,
    ControlledPropagationProtocolCapture,
    capture_controlled_propagation_protocol,
)
from sentinel_x.detection.systemd import SystemdServiceStateDetector
from sentinel_x.lab.injector import SystemdLabFaultInjector
from sentinel_x.lab.models import FaultExperimentManifest
from sentinel_x.systemd.boot import read_current_boot_id
from sentinel_x.systemd.reader import SystemctlServiceReader

CONTROLLED_PROPAGATION_BOUND_EXECUTION_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.controlled-propagation-bound-execution.v1"
)

_BACKEND_PROFILE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-execution-backend-profile.v1\x00"
)
_ATTEMPT_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-bound-execution-attempt.v1\x00"
)
_EXECUTION_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-bound-execution.v1\x00"
)
_LIVE_FINGERPRINT_DOMAIN: Final[bytes] = (
    b"sentinel-x.controlled-propagation-bound-live-fingerprint.v1\x00"
)

_STANDARD_INJECTOR_SYSTEMCTL_PATH: Final[str] = "/usr/bin/systemctl"
_DEFAULT_SERVICE_READ_TIMEOUT_SECONDS: Final[float] = 3.0
_DEFAULT_DEPENDENCY_READ_TIMEOUT_SECONDS: Final[float] = 3.0
_DEFAULT_LIFECYCLE_COMMAND_TIMEOUT_SECONDS: Final[float] = 5.0
_DEFAULT_LIFECYCLE_STATE_TIMEOUT_SECONDS: Final[float] = 10.0
_DEFAULT_LIFECYCLE_POLL_INTERVAL_SECONDS: Final[float] = 0.1
_DEFAULT_INJECTOR_COMMAND_TIMEOUT_SECONDS: Final[float] = 5.0
_DEFAULT_INJECTOR_POLL_INTERVAL_SECONDS: Final[float] = 0.05


class ControlledPropagationBoundExecutionError(RuntimeError):
    """Base error for Phase 5E.4 protocol-bound live execution."""


class ControlledPropagationBoundExecutionContractError(
    ControlledPropagationBoundExecutionError
):
    """Raised when execution binding inputs or derived identities drift."""


class ControlledPropagationBoundExecutionPreconditionError(
    ControlledPropagationBoundExecutionError
):
    """Raised before the bound live runner may mutate controlled units."""


class ControlledPropagationBoundExecutionRunError(
    ControlledPropagationBoundExecutionError
):
    """Preserve the bound attempt when the underlying live runner fails."""

    def __init__(
        self,
        attempt: ControlledPropagationBoundExecutionAttempt,
        cause: BaseException,
    ) -> None:
        self.attempt = attempt
        self.cause_type = type(cause).__name__
        super().__init__(
            "protocol-bound live execution failed after attempt binding; "
            f"cause_type={self.cause_type}"
        )


class ControlledPropagationExecutionBackendScope(StrEnum):
    """Exact scope represented by the execution backend profile."""

    SENTINEL_X_STANDARD_LIVE_BACKEND = "sentinel_x_standard_live_backend"


class ControlledPropagationExecutionBindingScope(StrEnum):
    """Scope of the ordering/binding claims established by Phase 5E.4."""

    BOUND_LIVE_RUN_INVOCATION = "bound_live_run_invocation"


@dataclass(frozen=True, slots=True)
class ControlledPropagationExecutionBackendProfile:
    """Content-addressed configuration for the standard controlled backend.

    The profile covers all caller/configurable backend parameters consumed by
    the Phase 5E.4 standard runner construction.  Host/kernel/systemd version,
    executable file bytes, ambient environment outside fixed subprocess
    settings, and external actors are intentionally outside this profile.
    """

    backend_profile_id: str
    service_systemctl_path: str = _STANDARD_INJECTOR_SYSTEMCTL_PATH
    dependency_systemctl_path: str = _STANDARD_INJECTOR_SYSTEMCTL_PATH
    lifecycle_systemctl_path: str = _STANDARD_INJECTOR_SYSTEMCTL_PATH
    injector_systemctl_path: str = _STANDARD_INJECTOR_SYSTEMCTL_PATH
    service_read_timeout_seconds: float = _DEFAULT_SERVICE_READ_TIMEOUT_SECONDS
    dependency_read_timeout_seconds: float = _DEFAULT_DEPENDENCY_READ_TIMEOUT_SECONDS
    lifecycle_command_timeout_seconds: float = (
        _DEFAULT_LIFECYCLE_COMMAND_TIMEOUT_SECONDS
    )
    lifecycle_state_timeout_seconds: float = _DEFAULT_LIFECYCLE_STATE_TIMEOUT_SECONDS
    lifecycle_poll_interval_seconds: float = _DEFAULT_LIFECYCLE_POLL_INTERVAL_SECONDS
    injector_command_timeout_seconds: float = _DEFAULT_INJECTOR_COMMAND_TIMEOUT_SECONDS
    injector_poll_interval_seconds: float = _DEFAULT_INJECTOR_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.backend_profile_id, "backendprof-"):
            raise ControlledPropagationBoundExecutionContractError(
                "backend_profile_id must be a backendprof- prefixed SHA-256 identity"
            )
        for field_name in (
            "service_systemctl_path",
            "dependency_systemctl_path",
            "lifecycle_systemctl_path",
            "injector_systemctl_path",
        ):
            normalized = _validate_absolute_path(
                getattr(self, field_name),
                field_name=field_name,
            )
            object.__setattr__(self, field_name, normalized)
        if self.injector_systemctl_path != _STANDARD_INJECTOR_SYSTEMCTL_PATH:
            raise ControlledPropagationBoundExecutionContractError(
                "frozen SystemdLabFaultInjector requires /usr/bin/systemctl"
            )
        object.__setattr__(
            self,
            "service_read_timeout_seconds",
            _validate_seconds(
                self.service_read_timeout_seconds,
                field_name="service_read_timeout_seconds",
                minimum=0.1,
                maximum=30.0,
            ),
        )
        object.__setattr__(
            self,
            "dependency_read_timeout_seconds",
            _validate_seconds(
                self.dependency_read_timeout_seconds,
                field_name="dependency_read_timeout_seconds",
                minimum=0.1,
                maximum=30.0,
            ),
        )
        object.__setattr__(
            self,
            "lifecycle_command_timeout_seconds",
            _validate_seconds(
                self.lifecycle_command_timeout_seconds,
                field_name="lifecycle_command_timeout_seconds",
                minimum=0.1,
                maximum=30.0,
            ),
        )
        object.__setattr__(
            self,
            "lifecycle_state_timeout_seconds",
            _validate_seconds(
                self.lifecycle_state_timeout_seconds,
                field_name="lifecycle_state_timeout_seconds",
                minimum=0.1,
                maximum=60.0,
            ),
        )
        object.__setattr__(
            self,
            "lifecycle_poll_interval_seconds",
            _validate_seconds(
                self.lifecycle_poll_interval_seconds,
                field_name="lifecycle_poll_interval_seconds",
                minimum=0.02,
                maximum=2.0,
            ),
        )
        object.__setattr__(
            self,
            "injector_command_timeout_seconds",
            _validate_seconds(
                self.injector_command_timeout_seconds,
                field_name="injector_command_timeout_seconds",
                minimum=0.1,
                maximum=30.0,
            ),
        )
        object.__setattr__(
            self,
            "injector_poll_interval_seconds",
            _validate_seconds(
                self.injector_poll_interval_seconds,
                field_name="injector_poll_interval_seconds",
                minimum=0.01,
                maximum=1.0,
            ),
        )
        expected = _backend_profile_id(self)
        if self.backend_profile_id != expected:
            raise ControlledPropagationBoundExecutionContractError(
                "backend_profile_id does not match backend configuration"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTROLLED_PROPAGATION_BOUND_EXECUTION_SCHEMA_VERSION,
            "backend_profile_id": self.backend_profile_id,
            "backend_scope": ControlledPropagationExecutionBackendScope.SENTINEL_X_STANDARD_LIVE_BACKEND.value,
            "service_systemctl_path": self.service_systemctl_path,
            "dependency_systemctl_path": self.dependency_systemctl_path,
            "lifecycle_systemctl_path": self.lifecycle_systemctl_path,
            "injector_systemctl_path": self.injector_systemctl_path,
            "service_read_timeout_seconds": self.service_read_timeout_seconds,
            "dependency_read_timeout_seconds": self.dependency_read_timeout_seconds,
            "lifecycle_command_timeout_seconds": self.lifecycle_command_timeout_seconds,
            "lifecycle_state_timeout_seconds": self.lifecycle_state_timeout_seconds,
            "lifecycle_poll_interval_seconds": self.lifecycle_poll_interval_seconds,
            "injector_command_timeout_seconds": self.injector_command_timeout_seconds,
            "injector_poll_interval_seconds": self.injector_poll_interval_seconds,
            "candidate_max_depth": 0,
            "candidate_max_units": 1,
            "service_detector_implementation": (
                "sentinel_x.detection.systemd.SystemdServiceStateDetector"
            ),
            "service_sampler_implementation": (
                "sentinel_x.dependency.propagation_live.SystemdDependentAssessmentSampler"
            ),
            "dependency_reader_implementation": (
                "sentinel_x.dependency.discovery.SystemctlDependencyUnitReader"
            ),
            "lifecycle_implementation": (
                "sentinel_x.dependency.propagation_live.SystemdPropagationPairLifecycle"
            ),
            "injector_implementation": "sentinel_x.lab.injector.SystemdLabFaultInjector",
            "boot_reader_implementation": (
                "sentinel_x.dependency.protocol_execution._read_bound_boot_id"
            ),
            "boot_reader_source_implementation": (
                "sentinel_x.systemd.boot.read_current_boot_id"
            ),
            "service_reader_clock_provider": "timezone_aware_utc_wall_clock",
            "dependency_reader_clock_provider": "timezone_aware_utc_wall_clock",
            "service_detector_wall_clock_provider": "timezone_aware_utc_wall_clock",
            "service_detector_monotonic_provider": "time.monotonic_ns",
            "injector_wall_clock_provider": "timezone_aware_utc_wall_clock",
            "injector_monotonic_provider": "time.monotonic",
            "injector_sleeper_provider": "time.sleep",
            "lifecycle_sleeper_provider": "time.sleep",
            "pair_verifier_implementation": (
                "sentinel_x.dependency.propagation_experiment."
                "verify_installed_systemd_propagation_pair"
            ),
            "subprocess_shell_invocation": False,
            "privilege_escalation_performed_by_backend": False,
            "unit_installation_performed_by_backend": False,
            "execution_backend_configuration_fully_captured": True,
            "execution_backend_configuration_scope_limited_to_sentinel_x": True,
            "host_environment_fully_captured": False,
            "systemd_manager_version_captured": False,
            "kernel_version_captured": False,
            "executable_path_existence_verified_by_profile": False,
            "executable_file_digest_captured": False,
            "external_actor_state_captured": False,
        }


@dataclass(frozen=True, slots=True)
class ControlledPropagationBoundExecutionAttempt:
    """Pre-invocation binding that survives even when the live run fails."""

    attempt_id: str
    protocol_capture: ControlledPropagationProtocolCapture
    backend_profile: ControlledPropagationExecutionBackendProfile
    boot_id_before_capture: str
    boot_id_after_capture: str
    boot_id_before_invocation: str
    effective_uid: int
    effective_gid: int
    live_runner_invoked_at: datetime
    live_runner_invoked_monotonic_usec: int

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.attempt_id, "protoattempt-"):
            raise ControlledPropagationBoundExecutionContractError(
                "attempt_id must be a protoattempt- prefixed SHA-256 identity"
            )
        if not isinstance(self.protocol_capture, ControlledPropagationProtocolCapture):
            raise ControlledPropagationBoundExecutionContractError(
                "protocol_capture must be a ControlledPropagationProtocolCapture"
            )
        if not isinstance(
            self.backend_profile,
            ControlledPropagationExecutionBackendProfile,
        ):
            raise ControlledPropagationBoundExecutionContractError(
                "backend_profile must be a ControlledPropagationExecutionBackendProfile"
            )
        _validate_boot_id(
            self.boot_id_before_capture, field_name="boot_id_before_capture"
        )
        _validate_boot_id(
            self.boot_id_after_capture, field_name="boot_id_after_capture"
        )
        _validate_boot_id(
            self.boot_id_before_invocation, field_name="boot_id_before_invocation"
        )
        if not (
            self.boot_id_before_capture
            == self.boot_id_after_capture
            == self.boot_id_before_invocation
        ):
            raise ControlledPropagationBoundExecutionPreconditionError(
                "boot changed across protocol capture or before live runner invocation"
            )
        _validate_nonnegative_int(self.effective_uid, field_name="effective_uid")
        _validate_nonnegative_int(self.effective_gid, field_name="effective_gid")
        _validate_aware_datetime(
            self.live_runner_invoked_at,
            field_name="live_runner_invoked_at",
        )
        _validate_nonnegative_int(
            self.live_runner_invoked_monotonic_usec,
            field_name="live_runner_invoked_monotonic_usec",
        )
        if self.protocol_capture.captured_at > self.live_runner_invoked_at:
            raise ControlledPropagationBoundExecutionContractError(
                "protocol capture wall time cannot follow live runner invocation"
            )
        if (
            self.protocol_capture.captured_monotonic_usec
            > self.live_runner_invoked_monotonic_usec
        ):
            raise ControlledPropagationBoundExecutionContractError(
                "protocol capture monotonic time cannot follow live runner invocation"
            )
        expected = _attempt_id(
            protocol_capture=self.protocol_capture,
            backend_profile=self.backend_profile,
            boot_id_before_capture=self.boot_id_before_capture,
            boot_id_after_capture=self.boot_id_after_capture,
            boot_id_before_invocation=self.boot_id_before_invocation,
            effective_uid=self.effective_uid,
            effective_gid=self.effective_gid,
            invoked_at=self.live_runner_invoked_at,
            invoked_monotonic_usec=self.live_runner_invoked_monotonic_usec,
        )
        if self.attempt_id != expected:
            raise ControlledPropagationBoundExecutionContractError(
                "attempt_id does not match exact pre-invocation binding"
            )

    @property
    def boot_id(self) -> str:
        return self.boot_id_before_capture

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": CONTROLLED_PROPAGATION_BOUND_EXECUTION_SCHEMA_VERSION,
            "binding_scope": ControlledPropagationExecutionBindingScope.BOUND_LIVE_RUN_INVOCATION.value,
            "attempt_id": self.attempt_id,
            "protocol_capture_id": self.protocol_capture.capture_id,
            "protocol_id": self.protocol_capture.protocol_id,
            "contrast_context_id": self.protocol_capture.contrast_context_id,
            "backend_profile_id": self.backend_profile.backend_profile_id,
            "boot_id_before_capture": self.boot_id_before_capture,
            "boot_id_after_capture": self.boot_id_after_capture,
            "boot_id_before_invocation": self.boot_id_before_invocation,
            "backend_scope": ControlledPropagationExecutionBackendScope.SENTINEL_X_STANDARD_LIVE_BACKEND.value,
            "execution_backend_configuration_scope_limited_to_sentinel_x": True,
            "effective_uid": self.effective_uid,
            "effective_gid": self.effective_gid,
            "live_runner_invoked_at": self.live_runner_invoked_at.isoformat(),
            "live_runner_invoked_monotonic_usec": (
                self.live_runner_invoked_monotonic_usec
            ),
            "capture_precedes_bound_live_runner_invocation_monotonic": True,
            "bound_runner_control_flow_orders_capture_before_mutation": True,
            "capture_before_external_mutation_claim": False,
            "capture_before_any_system_mutation_claim": False,
            "cryptographic_execution_attestation_assigned": False,
            "content_addressed_identity_is_authentication": False,
            "boot_stable_across_capture": True,
            "boot_stable_through_pre_invocation": True,
            "privilege_context_captured": True,
            "least_privilege_claim_assigned": False,
            "execution_backend_configuration_fully_captured": True,
            "host_environment_fully_captured": False,
            "execution_completed": False,
            "replication_claim_assigned": False,
            "statistical_significance_assigned": False,
            "causal_effect_estimate_assigned": False,
            "treatment_effect_claim_assigned": False,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "probabilistic_confidence_assigned": False,
            "scalar_score_assigned": False,
        }


@dataclass(frozen=True, slots=True)
class ControlledPropagationProtocolBoundExecution:
    """One successful live run exactly bound to capture/backend/boot context."""

    execution_id: str
    attempt: ControlledPropagationBoundExecutionAttempt
    live_run: ControlledPropagationLiveRun
    live_fingerprint: str
    live_runner_completed_at: datetime
    live_runner_completed_monotonic_usec: int

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.execution_id, "protoexec-"):
            raise ControlledPropagationBoundExecutionContractError(
                "execution_id must be a protoexec- prefixed SHA-256 identity"
            )
        if not isinstance(self.attempt, ControlledPropagationBoundExecutionAttempt):
            raise ControlledPropagationBoundExecutionContractError(
                "attempt must be a ControlledPropagationBoundExecutionAttempt"
            )
        if not isinstance(self.live_run, ControlledPropagationLiveRun):
            raise ControlledPropagationBoundExecutionContractError(
                "live_run must be a ControlledPropagationLiveRun"
            )
        if not _is_prefixed_digest(self.live_fingerprint, "livefp-"):
            raise ControlledPropagationBoundExecutionContractError(
                "live_fingerprint must be a livefp- prefixed SHA-256 identity"
            )
        _validate_aware_datetime(
            self.live_runner_completed_at,
            field_name="live_runner_completed_at",
        )
        _validate_nonnegative_int(
            self.live_runner_completed_monotonic_usec,
            field_name="live_runner_completed_monotonic_usec",
        )
        if self.live_runner_completed_at < self.attempt.live_runner_invoked_at:
            raise ControlledPropagationBoundExecutionContractError(
                "live runner completion wall time cannot precede invocation"
            )
        if (
            self.live_runner_completed_monotonic_usec
            < self.attempt.live_runner_invoked_monotonic_usec
        ):
            raise ControlledPropagationBoundExecutionContractError(
                "live runner completion monotonic time cannot precede invocation"
            )
        _validate_live_run_binding(self.attempt, self.live_run)
        expected_live_fingerprint = _live_fingerprint(self.live_run)
        if self.live_fingerprint != expected_live_fingerprint:
            raise ControlledPropagationBoundExecutionContractError(
                "live_fingerprint does not match bound live run"
            )
        expected_execution_id = _execution_id(
            attempt_id=self.attempt.attempt_id,
            live_fingerprint=self.live_fingerprint,
            completed_at=self.live_runner_completed_at,
            completed_monotonic_usec=self.live_runner_completed_monotonic_usec,
        )
        if self.execution_id != expected_execution_id:
            raise ControlledPropagationBoundExecutionContractError(
                "execution_id does not match exact bound execution provenance"
            )

    def to_dict(self) -> dict[str, object]:
        run = self.live_run
        return {
            "schema_version": CONTROLLED_PROPAGATION_BOUND_EXECUTION_SCHEMA_VERSION,
            "binding_scope": ControlledPropagationExecutionBindingScope.BOUND_LIVE_RUN_INVOCATION.value,
            "execution_id": self.execution_id,
            "attempt_id": self.attempt.attempt_id,
            "protocol_capture_id": self.attempt.protocol_capture.capture_id,
            "protocol_id": self.attempt.protocol_capture.protocol_id,
            "backend_profile_id": self.attempt.backend_profile.backend_profile_id,
            "backend_scope": ControlledPropagationExecutionBackendScope.SENTINEL_X_STANDARD_LIVE_BACKEND.value,
            "live_fingerprint": self.live_fingerprint,
            "live_runner_completed_at": self.live_runner_completed_at.isoformat(),
            "live_runner_completed_monotonic_usec": (
                self.live_runner_completed_monotonic_usec
            ),
            "boot_id": self.attempt.boot_id,
            "boot_id_before_capture": self.attempt.boot_id_before_capture,
            "boot_id_after_capture": self.attempt.boot_id_after_capture,
            "boot_id_before_invocation": self.attempt.boot_id_before_invocation,
            "live_run_boot_id_before": run.boot_id_before,
            "live_run_boot_id_after": run.boot_id_after,
            "pair_id": run.pair_artifact.pair_id,
            "experiment_record_id": run.experiment_record.record_id,
            "candidate_id": run.candidate.candidate_id,
            "topology_id": run.candidate.topology_id,
            "graph_version_id": run.candidate.graph_version_id,
            "dependent_assessment_count": len(run.dependent_assessments),
            "evidence_class": run.experiment_record.evidence_class.value,
            "post_recovery_verified": run.post_recovery_verified,
            "protocol_capture_matches_live_pair": True,
            "protocol_capture_matches_live_policy": True,
            "protocol_capture_matches_live_manifest_outcome_identity": True,
            "capture_precedes_bound_live_runner_invocation_monotonic": True,
            "bound_runner_control_flow_orders_capture_before_mutation": True,
            "bound_runner_control_flow_orders_capture_before_fault_ground_truth": True,
            "same_boot_bound_across_capture_and_live_run": True,
            "execution_backend_configuration_fully_captured": True,
            "execution_backend_configuration_scope_limited_to_sentinel_x": True,
            "host_environment_fully_captured": False,
            "external_actor_state_captured": False,
            "cryptographic_execution_attestation_assigned": False,
            "content_addressed_identity_is_authentication": False,
            "effective_uid": self.attempt.effective_uid,
            "effective_gid": self.attempt.effective_gid,
            "least_privilege_claim_assigned": False,
            "unit_installation_performed_by_runner": False,
            "privilege_escalation_performed_by_runner": False,
            "shell_invocation_performed_by_runner": False,
            "execution_completed": True,
            "replication_claim_assigned": False,
            "statistical_significance_assigned": False,
            "causal_effect_estimate_assigned": False,
            "treatment_effect_claim_assigned": False,
            "experiment_wide_single_variable_isolation_claim": False,
            "generalization_beyond_bound_execution_permitted": False,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "universal_systemd_behavior_claim": False,
            "probabilistic_confidence_assigned": False,
            "scalar_score_assigned": False,
        }


class ControlledPropagationProtocolBoundRunner:
    """Capture protocol first, then invoke one standard profiled live runner."""

    def __init__(
        self,
        *,
        backend_profile: ControlledPropagationExecutionBackendProfile | None = None,
    ) -> None:
        self._backend_profile = (
            build_controlled_propagation_execution_backend_profile()
            if backend_profile is None
            else backend_profile
        )
        if not isinstance(
            self._backend_profile,
            ControlledPropagationExecutionBackendProfile,
        ):
            raise TypeError(
                "backend_profile must be a ControlledPropagationExecutionBackendProfile"
            )

    @property
    def backend_profile(self) -> ControlledPropagationExecutionBackendProfile:
        return self._backend_profile

    def run(
        self,
        pair_artifact: SystemdPropagationPairArtifact,
        manifest: FaultExperimentManifest,
        policy: ControlledPropagationLivePolicy,
    ) -> ControlledPropagationProtocolBoundExecution:
        _validate_bound_inputs(pair_artifact, manifest, policy)

        boot_before_capture = _read_normalized_boot_id()
        protocol_capture = capture_controlled_propagation_protocol(
            pair_artifact,
            manifest,
            policy,
        )
        boot_after_capture = _read_normalized_boot_id()
        if boot_before_capture != boot_after_capture:
            raise ControlledPropagationBoundExecutionPreconditionError(
                "boot changed while protocol inputs were being captured"
            )

        runner = _build_standard_live_runner(
            self._backend_profile,
            expected_boot_id=boot_before_capture,
        )
        boot_before_invocation = _read_normalized_boot_id()
        if boot_before_invocation != boot_before_capture:
            raise ControlledPropagationBoundExecutionPreconditionError(
                "boot changed after protocol capture before live runner invocation"
            )

        invoked_at = datetime.now(timezone.utc)
        invoked_monotonic_usec = time.monotonic_ns() // 1_000
        if protocol_capture.captured_monotonic_usec > invoked_monotonic_usec:
            raise ControlledPropagationBoundExecutionContractError(
                "protocol capture monotonic time follows bound runner invocation"
            )
        attempt = _build_attempt(
            protocol_capture=protocol_capture,
            backend_profile=self._backend_profile,
            boot_id_before_capture=boot_before_capture,
            boot_id_after_capture=boot_after_capture,
            boot_id_before_invocation=boot_before_invocation,
            effective_uid=os.geteuid(),
            effective_gid=os.getegid(),
            invoked_at=invoked_at,
            invoked_monotonic_usec=invoked_monotonic_usec,
        )

        try:
            live_run = runner.run(pair_artifact, manifest, policy)
            completed_at = datetime.now(timezone.utc)
            completed_monotonic_usec = time.monotonic_ns() // 1_000
            _validate_live_run_binding(attempt, live_run)
            live_fingerprint = _live_fingerprint(live_run)
            execution_id = _execution_id(
                attempt_id=attempt.attempt_id,
                live_fingerprint=live_fingerprint,
                completed_at=completed_at,
                completed_monotonic_usec=completed_monotonic_usec,
            )
            return ControlledPropagationProtocolBoundExecution(
                execution_id=execution_id,
                attempt=attempt,
                live_run=live_run,
                live_fingerprint=live_fingerprint,
                live_runner_completed_at=completed_at,
                live_runner_completed_monotonic_usec=completed_monotonic_usec,
            )
        except Exception as exc:
            raise ControlledPropagationBoundExecutionRunError(attempt, exc) from exc


def build_controlled_propagation_execution_backend_profile(
    *,
    service_systemctl_path: str | Path = _STANDARD_INJECTOR_SYSTEMCTL_PATH,
    dependency_systemctl_path: str | Path = _STANDARD_INJECTOR_SYSTEMCTL_PATH,
    lifecycle_systemctl_path: str | Path = _STANDARD_INJECTOR_SYSTEMCTL_PATH,
    service_read_timeout_seconds: float = _DEFAULT_SERVICE_READ_TIMEOUT_SECONDS,
    dependency_read_timeout_seconds: float = _DEFAULT_DEPENDENCY_READ_TIMEOUT_SECONDS,
    lifecycle_command_timeout_seconds: float = _DEFAULT_LIFECYCLE_COMMAND_TIMEOUT_SECONDS,
    lifecycle_state_timeout_seconds: float = _DEFAULT_LIFECYCLE_STATE_TIMEOUT_SECONDS,
    lifecycle_poll_interval_seconds: float = _DEFAULT_LIFECYCLE_POLL_INTERVAL_SECONDS,
    injector_command_timeout_seconds: float = _DEFAULT_INJECTOR_COMMAND_TIMEOUT_SECONDS,
    injector_poll_interval_seconds: float = _DEFAULT_INJECTOR_POLL_INTERVAL_SECONDS,
) -> ControlledPropagationExecutionBackendProfile:
    """Build one exact standard-backend profile from explicit bounded settings."""

    normalized_service_systemctl_path = _validate_absolute_path(
        service_systemctl_path,
        field_name="service_systemctl_path",
    )
    normalized_dependency_systemctl_path = _validate_absolute_path(
        dependency_systemctl_path,
        field_name="dependency_systemctl_path",
    )
    normalized_lifecycle_systemctl_path = _validate_absolute_path(
        lifecycle_systemctl_path,
        field_name="lifecycle_systemctl_path",
    )
    normalized_service_read_timeout_seconds = _validate_seconds(
        service_read_timeout_seconds,
        field_name="service_read_timeout_seconds",
        minimum=0.1,
        maximum=30.0,
    )
    normalized_dependency_read_timeout_seconds = _validate_seconds(
        dependency_read_timeout_seconds,
        field_name="dependency_read_timeout_seconds",
        minimum=0.1,
        maximum=30.0,
    )
    normalized_lifecycle_command_timeout_seconds = _validate_seconds(
        lifecycle_command_timeout_seconds,
        field_name="lifecycle_command_timeout_seconds",
        minimum=0.1,
        maximum=30.0,
    )
    normalized_lifecycle_state_timeout_seconds = _validate_seconds(
        lifecycle_state_timeout_seconds,
        field_name="lifecycle_state_timeout_seconds",
        minimum=0.1,
        maximum=60.0,
    )
    normalized_lifecycle_poll_interval_seconds = _validate_seconds(
        lifecycle_poll_interval_seconds,
        field_name="lifecycle_poll_interval_seconds",
        minimum=0.02,
        maximum=2.0,
    )
    normalized_injector_command_timeout_seconds = _validate_seconds(
        injector_command_timeout_seconds,
        field_name="injector_command_timeout_seconds",
        minimum=0.1,
        maximum=30.0,
    )
    normalized_injector_poll_interval_seconds = _validate_seconds(
        injector_poll_interval_seconds,
        field_name="injector_poll_interval_seconds",
        minimum=0.01,
        maximum=1.0,
    )

    profile_without_id: dict[str, object] = {
        "service_systemctl_path": normalized_service_systemctl_path,
        "dependency_systemctl_path": normalized_dependency_systemctl_path,
        "lifecycle_systemctl_path": normalized_lifecycle_systemctl_path,
        "injector_systemctl_path": _STANDARD_INJECTOR_SYSTEMCTL_PATH,
        "service_read_timeout_seconds": normalized_service_read_timeout_seconds,
        "dependency_read_timeout_seconds": normalized_dependency_read_timeout_seconds,
        "lifecycle_command_timeout_seconds": normalized_lifecycle_command_timeout_seconds,
        "lifecycle_state_timeout_seconds": normalized_lifecycle_state_timeout_seconds,
        "lifecycle_poll_interval_seconds": normalized_lifecycle_poll_interval_seconds,
        "injector_command_timeout_seconds": normalized_injector_command_timeout_seconds,
        "injector_poll_interval_seconds": normalized_injector_poll_interval_seconds,
    }
    backend_profile_id = "backendprof-" + _content_digest(
        _BACKEND_PROFILE_ID_DOMAIN,
        _backend_payload(profile_without_id),
    )
    return ControlledPropagationExecutionBackendProfile(
        backend_profile_id=backend_profile_id,
        service_systemctl_path=normalized_service_systemctl_path,
        dependency_systemctl_path=normalized_dependency_systemctl_path,
        lifecycle_systemctl_path=normalized_lifecycle_systemctl_path,
        injector_systemctl_path=_STANDARD_INJECTOR_SYSTEMCTL_PATH,
        service_read_timeout_seconds=normalized_service_read_timeout_seconds,
        dependency_read_timeout_seconds=normalized_dependency_read_timeout_seconds,
        lifecycle_command_timeout_seconds=normalized_lifecycle_command_timeout_seconds,
        lifecycle_state_timeout_seconds=normalized_lifecycle_state_timeout_seconds,
        lifecycle_poll_interval_seconds=normalized_lifecycle_poll_interval_seconds,
        injector_command_timeout_seconds=normalized_injector_command_timeout_seconds,
        injector_poll_interval_seconds=normalized_injector_poll_interval_seconds,
    )


def _build_standard_live_runner(
    profile: ControlledPropagationExecutionBackendProfile,
    *,
    expected_boot_id: str,
) -> ControlledPropagationLiveRunner:
    _validate_boot_id(expected_boot_id, field_name="expected_boot_id")
    service_reader = SystemctlServiceReader(
        systemctl_path=profile.service_systemctl_path,
        timeout_seconds=profile.service_read_timeout_seconds,
    )
    lifecycle = SystemdPropagationPairLifecycle(
        reader=service_reader,
        systemctl_path=profile.lifecycle_systemctl_path,
        command_timeout_seconds=profile.lifecycle_command_timeout_seconds,
        state_timeout_seconds=profile.lifecycle_state_timeout_seconds,
        poll_interval_seconds=profile.lifecycle_poll_interval_seconds,
        pair_verifier=verify_installed_systemd_propagation_pair,
    )
    injector = SystemdLabFaultInjector(
        command_timeout_seconds=profile.injector_command_timeout_seconds,
        poll_interval_seconds=profile.injector_poll_interval_seconds,
    )
    candidate_provider = _ProfiledCandidateProvider(profile)
    sampler_factory = _ProfiledSamplerFactory(profile)
    return ControlledPropagationLiveRunner(
        pair_verifier=verify_installed_systemd_propagation_pair,
        boot_id_reader=lambda: _read_bound_boot_id(expected_boot_id),
        candidate_provider=candidate_provider,
        sampler_factory=sampler_factory,
        injector=injector,
        lifecycle=lifecycle,
    )


class _ProfiledCandidateProvider:
    def __init__(self, profile: ControlledPropagationExecutionBackendProfile) -> None:
        self._profile = profile

    def __call__(
        self,
        artifact: SystemdPropagationPairArtifact,
        expected_boot_id: str,
    ) -> DependencyPropagationCandidate:
        reader = SystemctlDependencyUnitReader(
            systemctl_path=self._profile.dependency_systemctl_path,
            timeout_seconds=self._profile.dependency_read_timeout_seconds,
        )
        report = SystemdDependencyDiscovery(
            reader=reader,
            max_depth=0,
            max_units=1,
            boot_id_reader=lambda: expected_boot_id,
        ).discover(artifact.dependent_unit)
        graph = build_dependency_graph(report)
        if graph.boot_id != expected_boot_id:
            raise ControlledPropagationLivePreconditionError(
                "profiled dependency graph boot does not match bound boot"
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
                "profiled manager view must expose exactly one pair requirement edge"
            )
        if requirement_edges[0].relation is not artifact.spec.requirement_relation:
            raise ControlledPropagationLivePreconditionError(
                "profiled manager requirement relation does not match pair artifact"
            )
        after_edges = tuple(
            edge for edge in pair_edges if edge.relation is DependencyRelation.AFTER
        )
        if len(after_edges) != 1:
            raise ControlledPropagationLivePreconditionError(
                "profiled manager view must expose exactly one controlled After edge"
            )
        unexpected_pair_edges = tuple(
            edge
            for edge in pair_edges
            if edge.relation
            not in {artifact.spec.requirement_relation, DependencyRelation.AFTER}
        )
        if unexpected_pair_edges:
            raise ControlledPropagationLivePreconditionError(
                "profiled manager view contains unexpected pair relation context"
            )
        candidates = tuple(
            candidate
            for candidate in build_dependency_propagation_candidates(graph)
            if candidate.dependency_unit == artifact.source_unit
            and candidate.dependent_unit == artifact.dependent_unit
        )
        if len(candidates) != 1:
            raise ControlledPropagationLivePreconditionError(
                "profiled graph must yield exactly one controlled propagation candidate"
            )
        return candidates[0]


class _ProfiledSamplerFactory:
    def __init__(self, profile: ControlledPropagationExecutionBackendProfile) -> None:
        self._profile = profile

    def __call__(self, unit_name: str) -> SystemdDependentAssessmentSampler:
        return SystemdDependentAssessmentSampler(
            unit_name,
            reader=SystemctlServiceReader(
                systemctl_path=self._profile.service_systemctl_path,
                timeout_seconds=self._profile.service_read_timeout_seconds,
            ),
            detector=SystemdServiceStateDetector(),
            boot_id_reader=read_current_boot_id,
        )


def _build_attempt(
    *,
    protocol_capture: ControlledPropagationProtocolCapture,
    backend_profile: ControlledPropagationExecutionBackendProfile,
    boot_id_before_capture: str,
    boot_id_after_capture: str,
    boot_id_before_invocation: str,
    effective_uid: int,
    effective_gid: int,
    invoked_at: datetime,
    invoked_monotonic_usec: int,
) -> ControlledPropagationBoundExecutionAttempt:
    return ControlledPropagationBoundExecutionAttempt(
        attempt_id=_attempt_id(
            protocol_capture=protocol_capture,
            backend_profile=backend_profile,
            boot_id_before_capture=boot_id_before_capture,
            boot_id_after_capture=boot_id_after_capture,
            boot_id_before_invocation=boot_id_before_invocation,
            effective_uid=effective_uid,
            effective_gid=effective_gid,
            invoked_at=invoked_at,
            invoked_monotonic_usec=invoked_monotonic_usec,
        ),
        protocol_capture=protocol_capture,
        backend_profile=backend_profile,
        boot_id_before_capture=boot_id_before_capture,
        boot_id_after_capture=boot_id_after_capture,
        boot_id_before_invocation=boot_id_before_invocation,
        effective_uid=effective_uid,
        effective_gid=effective_gid,
        live_runner_invoked_at=invoked_at,
        live_runner_invoked_monotonic_usec=invoked_monotonic_usec,
    )


def _validate_bound_inputs(
    pair_artifact: SystemdPropagationPairArtifact,
    manifest: FaultExperimentManifest,
    policy: ControlledPropagationLivePolicy,
) -> None:
    if not isinstance(pair_artifact, SystemdPropagationPairArtifact):
        raise ControlledPropagationBoundExecutionContractError(
            "pair_artifact must be a SystemdPropagationPairArtifact"
        )
    if not isinstance(manifest, FaultExperimentManifest):
        raise ControlledPropagationBoundExecutionContractError(
            "manifest must be a FaultExperimentManifest"
        )
    if not isinstance(policy, ControlledPropagationLivePolicy):
        raise ControlledPropagationBoundExecutionContractError(
            "policy must be a ControlledPropagationLivePolicy"
        )
    if manifest.scenario.target_unit != pair_artifact.source_unit:
        raise ControlledPropagationBoundExecutionContractError(
            "manifest must target the controlled pair source"
        )


def _validate_live_run_binding(
    attempt: ControlledPropagationBoundExecutionAttempt,
    live_run: ControlledPropagationLiveRun,
) -> None:
    capture = attempt.protocol_capture
    if live_run.pair_artifact != capture.pair_artifact:
        raise ControlledPropagationBoundExecutionContractError(
            "live run pair artifact does not match protocol capture"
        )
    if live_run.policy != capture.policy:
        raise ControlledPropagationBoundExecutionContractError(
            "live run policy does not match protocol capture"
        )
    if (
        live_run.boot_id_before != attempt.boot_id
        or live_run.boot_id_after != attempt.boot_id
    ):
        raise ControlledPropagationBoundExecutionContractError(
            "live run boot does not match capture-bound boot"
        )
    outcome = live_run.injection_outcome
    manifest = capture.manifest
    if outcome.plan.experiment_id != manifest.experiment_id:
        raise ControlledPropagationBoundExecutionContractError(
            "live run experiment identity does not match captured manifest"
        )
    if outcome.plan.scenario_id != manifest.scenario.scenario_id:
        raise ControlledPropagationBoundExecutionContractError(
            "live run scenario identity does not match captured manifest"
        )
    if outcome.plan.target_unit != manifest.scenario.target_unit:
        raise ControlledPropagationBoundExecutionContractError(
            "live run target does not match captured manifest"
        )
    if outcome.plan.fault_mode is not manifest.scenario.fault_mode:
        raise ControlledPropagationBoundExecutionContractError(
            "live run fault mode does not match captured manifest"
        )
    if (
        outcome.plan.expected_fault_active_states
        != manifest.scenario.expected_fault_active_states
    ):
        raise ControlledPropagationBoundExecutionContractError(
            "live run expected fault states do not match captured manifest"
        )
    if not live_run.post_recovery_verified:
        raise ControlledPropagationBoundExecutionContractError(
            "bound live execution requires verified recovery"
        )


def _backend_profile_id(profile: ControlledPropagationExecutionBackendProfile) -> str:
    values = {
        "service_systemctl_path": profile.service_systemctl_path,
        "dependency_systemctl_path": profile.dependency_systemctl_path,
        "lifecycle_systemctl_path": profile.lifecycle_systemctl_path,
        "injector_systemctl_path": profile.injector_systemctl_path,
        "service_read_timeout_seconds": profile.service_read_timeout_seconds,
        "dependency_read_timeout_seconds": profile.dependency_read_timeout_seconds,
        "lifecycle_command_timeout_seconds": profile.lifecycle_command_timeout_seconds,
        "lifecycle_state_timeout_seconds": profile.lifecycle_state_timeout_seconds,
        "lifecycle_poll_interval_seconds": profile.lifecycle_poll_interval_seconds,
        "injector_command_timeout_seconds": profile.injector_command_timeout_seconds,
        "injector_poll_interval_seconds": profile.injector_poll_interval_seconds,
    }
    return "backendprof-" + _content_digest(
        _BACKEND_PROFILE_ID_DOMAIN,
        _backend_payload(values),
    )


def _backend_payload(values: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": CONTROLLED_PROPAGATION_BOUND_EXECUTION_SCHEMA_VERSION,
        "backend_scope": ControlledPropagationExecutionBackendScope.SENTINEL_X_STANDARD_LIVE_BACKEND.value,
        "controlled_propagation_protocol_schema_version": (
            CONTROLLED_PROPAGATION_PROTOCOL_SCHEMA_VERSION
        ),
        "controlled_propagation_live_schema_version": (
            CONTROLLED_PROPAGATION_LIVE_SCHEMA_VERSION
        ),
        "candidate_max_depth": 0,
        "candidate_max_units": 1,
        "service_detector_implementation": (
            "sentinel_x.detection.systemd.SystemdServiceStateDetector"
        ),
        "service_sampler_implementation": (
            "sentinel_x.dependency.propagation_live.SystemdDependentAssessmentSampler"
        ),
        "dependency_reader_implementation": (
            "sentinel_x.dependency.discovery.SystemctlDependencyUnitReader"
        ),
        "lifecycle_implementation": (
            "sentinel_x.dependency.propagation_live.SystemdPropagationPairLifecycle"
        ),
        "injector_implementation": "sentinel_x.lab.injector.SystemdLabFaultInjector",
        "boot_reader_implementation": (
            "sentinel_x.dependency.protocol_execution._read_bound_boot_id"
        ),
        "boot_reader_source_implementation": (
            "sentinel_x.systemd.boot.read_current_boot_id"
        ),
        "service_reader_clock_provider": "timezone_aware_utc_wall_clock",
        "dependency_reader_clock_provider": "timezone_aware_utc_wall_clock",
        "service_detector_wall_clock_provider": "timezone_aware_utc_wall_clock",
        "service_detector_monotonic_provider": "time.monotonic_ns",
        "injector_wall_clock_provider": "timezone_aware_utc_wall_clock",
        "injector_monotonic_provider": "time.monotonic",
        "injector_sleeper_provider": "time.sleep",
        "lifecycle_sleeper_provider": "time.sleep",
        "pair_verifier_implementation": (
            "sentinel_x.dependency.propagation_experiment."
            "verify_installed_systemd_propagation_pair"
        ),
        "subprocess_shell_invocation": False,
        "privilege_escalation_performed_by_backend": False,
        "unit_installation_performed_by_backend": False,
        **values,
    }


def _attempt_id(
    *,
    protocol_capture: ControlledPropagationProtocolCapture,
    backend_profile: ControlledPropagationExecutionBackendProfile,
    boot_id_before_capture: str,
    boot_id_after_capture: str,
    boot_id_before_invocation: str,
    effective_uid: int,
    effective_gid: int,
    invoked_at: datetime,
    invoked_monotonic_usec: int,
) -> str:
    payload: dict[str, object] = {
        "protocol_capture_id": protocol_capture.capture_id,
        "protocol_id": protocol_capture.protocol_id,
        "backend_profile_id": backend_profile.backend_profile_id,
        "boot_id_before_capture": boot_id_before_capture,
        "boot_id_after_capture": boot_id_after_capture,
        "boot_id_before_invocation": boot_id_before_invocation,
        "effective_uid": effective_uid,
        "effective_gid": effective_gid,
        "live_runner_invoked_at": invoked_at.isoformat(),
        "live_runner_invoked_monotonic_usec": invoked_monotonic_usec,
    }
    return "protoattempt-" + _content_digest(_ATTEMPT_ID_DOMAIN, payload)


def _live_fingerprint(live_run: ControlledPropagationLiveRun) -> str:
    payload: dict[str, object] = {
        "live_schema_version": CONTROLLED_PROPAGATION_LIVE_SCHEMA_VERSION,
        "pair_id": live_run.pair_artifact.pair_id,
        "policy": live_run.policy.to_dict(),
        "candidate_id": live_run.candidate.candidate_id,
        "graph_version_id": live_run.candidate.graph_version_id,
        "topology_id": live_run.candidate.topology_id,
        "boot_id_before": live_run.boot_id_before,
        "boot_id_after": live_run.boot_id_after,
        "dependent_assessment_evidence_ids": [
            evidence.evidence_id for evidence in live_run.dependent_assessments
        ],
        "experiment_record_id": live_run.experiment_record.record_id,
        "injection_outcome": live_run.injection_outcome.to_dict(),
        "post_recovery_verified": live_run.post_recovery_verified,
    }
    return "livefp-" + _content_digest(_LIVE_FINGERPRINT_DOMAIN, payload)


def _execution_id(
    *,
    attempt_id: str,
    live_fingerprint: str,
    completed_at: datetime,
    completed_monotonic_usec: int,
) -> str:
    payload: dict[str, object] = {
        "attempt_id": attempt_id,
        "live_fingerprint": live_fingerprint,
        "live_runner_completed_at": completed_at.isoformat(),
        "live_runner_completed_monotonic_usec": completed_monotonic_usec,
    }
    return "protoexec-" + _content_digest(_EXECUTION_ID_DOMAIN, payload)


def _read_normalized_boot_id() -> str:
    boot_id = read_current_boot_id()
    _validate_boot_id(boot_id, field_name="boot_id")
    return boot_id


def _read_bound_boot_id(expected_boot_id: str) -> str:
    _validate_boot_id(expected_boot_id, field_name="expected_boot_id")
    current_boot_id = _read_normalized_boot_id()
    if current_boot_id != expected_boot_id:
        raise ControlledPropagationBoundExecutionPreconditionError(
            "boot changed after protocol capture before or during bound live execution"
        )
    return current_boot_id


def _validate_boot_id(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be a string"
        )
    normalized = value.strip().lower().replace("-", "")
    if (
        normalized != value
        or len(value) != 32
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be a normalized 32-hex boot identity"
        )


def _validate_absolute_path(value: str | os.PathLike[str], *, field_name: str) -> str:
    try:
        normalized = os.fspath(value)
    except TypeError as exc:
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be path-like"
        ) from exc
    if not isinstance(normalized, str):
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must resolve to a string path"
        )
    if not normalized or "\x00" in normalized or not os.path.isabs(normalized):
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be an absolute NUL-free path"
        )
    return normalized


def _validate_seconds(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be a real number"
        )
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be between {minimum:g} and {maximum:g} seconds"
        )
    return normalized


def _validate_nonnegative_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be a non-negative integer"
        )


def _validate_aware_datetime(value: object, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be a datetime"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise ControlledPropagationBoundExecutionContractError(
            f"{field_name} must be timezone-aware"
        )


def _is_prefixed_digest(value: object, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value[len(prefix) :]
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _content_digest(domain: bytes, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(domain + encoded).hexdigest()
