from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.dependency.discovery import (
    SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
    DiscoveredSystemdUnit,
    SystemdDependencyDiscoveryReport,
    SystemdDependencyUnitSnapshot,
)
from sentinel_x.dependency.graph import build_dependency_graph
from sentinel_x.dependency.models import (
    DependencyConfigurationOrigin,
    DependencyEndpoint,
    DependencyEntityKind,
    DependencyEvidence,
    DependencyEvidenceOrigin,
    DependencyRelation,
)
from sentinel_x.dependency.propagation import (
    bind_systemd_assessment_evidence,
    build_dependency_propagation_candidates,
)
from sentinel_x.dependency.propagation_experiment import (
    SystemdPropagationPairSpec,
    build_controlled_propagation_experiment_record,
    build_systemd_propagation_pair,
    evaluate_controlled_ground_truth_coverage,
)
from sentinel_x.dependency.propagation_live import (
    ControlledPropagationLivePolicy,
    ControlledPropagationLivePreconditionError,
    ControlledPropagationLiveRun,
    ControlledPropagationLiveRunner,
)
from sentinel_x.dependency.protocol import capture_controlled_propagation_protocol
from sentinel_x.dependency.protocol_execution import (
    CONTROLLED_PROPAGATION_BOUND_EXECUTION_SCHEMA_VERSION,
    ControlledPropagationBoundExecutionContractError,
    ControlledPropagationBoundExecutionPreconditionError,
    ControlledPropagationBoundExecutionRunError,
    ControlledPropagationExecutionBackendProfile,
    ControlledPropagationExecutionBackendScope,
    ControlledPropagationExecutionBindingScope,
    ControlledPropagationProtocolBoundExecution,
    ControlledPropagationProtocolBoundRunner,
    build_controlled_propagation_execution_backend_profile,
)
from sentinel_x.detection.models import (
    DetectionBasis,
    SystemdServiceHealthAssessment,
    SystemdServiceHealthStatus,
)
from sentinel_x.lab.fixture import SystemdLabFixtureSpec
from sentinel_x.lab.injector import (
    FaultInjectionOutcome,
    LabFaultOperation,
    SystemdLabFaultPlan,
    SystemdLabServiceState,
)
from sentinel_x.lab.models import (
    FaultExperimentManifest,
    FaultGroundTruthWindow,
    FaultMode,
    FaultScenario,
)

_BOOT = "a" * 32
_OTHER_BOOT = "b" * 32
_TIME = datetime(2026, 8, 16, 7, 30, tzinfo=timezone.utc)
_START_USEC = 2_000_000


def _artifact(relation: DependencyRelation = DependencyRelation.REQUIRES):
    return build_systemd_propagation_pair(
        SystemdPropagationPairSpec(
            source=SystemdLabFixtureSpec(
                "p5e4-source",
                runtime_max_seconds=120,
            ),
            dependent=SystemdLabFixtureSpec(
                "p5e4-dependent",
                runtime_max_seconds=120,
            ),
            requirement_relation=relation,
        )
    )


def _manifest(
    artifact,
    *,
    digit: str = "1",
    scenario_id: str = "p5e4-requires-stop",
    fault_timeout_seconds: float = 5.0,
) -> FaultExperimentManifest:
    return FaultExperimentManifest(
        scenario=FaultScenario(
            scenario_id=scenario_id,
            description="Phase 5E.4 protocol-bound execution fixture.",
            target_unit=artifact.source_unit,
            fault_mode=FaultMode.SERVICE_INACTIVE,
            baseline_timeout_seconds=5.0,
            fault_timeout_seconds=fault_timeout_seconds,
            recovery_timeout_seconds=10.0,
            evidence_grace_seconds=1.0,
        ),
        experiment_id="exp-" + digit * 32,
        created_at=_TIME - timedelta(seconds=5),
    )


def _policy(
    *,
    max_sample_gap_usec: int = 300_000,
    sample_interval_seconds: float = 0.1,
    max_samples: int = 32,
    capture_join_timeout_seconds: float = 5.0,
) -> ControlledPropagationLivePolicy:
    return ControlledPropagationLivePolicy(
        max_sample_gap_usec=max_sample_gap_usec,
        sample_interval_seconds=sample_interval_seconds,
        max_samples=max_samples,
        capture_join_timeout_seconds=capture_join_timeout_seconds,
    )


def _report_for(
    artifact,
    *,
    relation: DependencyRelation | None = None,
    include_after: bool = True,
    include_before: bool = False,
    boot_id: str = _BOOT,
) -> SystemdDependencyDiscoveryReport:
    selected_relation = relation or artifact.spec.requirement_relation
    event_id = f"dep-event-{selected_relation.value}-p5e4-report"
    observed_at = _TIME - timedelta(seconds=1)
    evidence = [
        DependencyEvidence(
            evidence_id="depev-"
            + ("4" if selected_relation is DependencyRelation.REQUIRES else "5") * 64,
            source_event_id=event_id,
            observed_at=observed_at,
            boot_id=boot_id,
            subject=DependencyEndpoint(
                kind=DependencyEntityKind.SYSTEMD_UNIT,
                identity=artifact.dependent_unit,
            ),
            relation=selected_relation,
            object=DependencyEndpoint(
                kind=DependencyEntityKind.SYSTEMD_UNIT,
                identity=artifact.source_unit,
            ),
            origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
            configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
            source_property=(
                "Requires"
                if selected_relation is DependencyRelation.REQUIRES
                else "Wants"
            ),
        )
    ]
    if include_after:
        evidence.append(
            DependencyEvidence(
                evidence_id="depev-" + "6" * 64,
                source_event_id=event_id,
                observed_at=observed_at,
                boot_id=boot_id,
                subject=DependencyEndpoint(
                    kind=DependencyEntityKind.SYSTEMD_UNIT,
                    identity=artifact.dependent_unit,
                ),
                relation=DependencyRelation.AFTER,
                object=DependencyEndpoint(
                    kind=DependencyEntityKind.SYSTEMD_UNIT,
                    identity=artifact.source_unit,
                ),
                origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
                configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
                source_property="After",
            )
        )
    if include_before:
        evidence.append(
            DependencyEvidence(
                evidence_id="depev-" + "7" * 64,
                source_event_id=event_id,
                observed_at=observed_at,
                boot_id=boot_id,
                subject=DependencyEndpoint(
                    kind=DependencyEntityKind.SYSTEMD_UNIT,
                    identity=artifact.dependent_unit,
                ),
                relation=DependencyRelation.BEFORE,
                object=DependencyEndpoint(
                    kind=DependencyEntityKind.SYSTEMD_UNIT,
                    identity=artifact.source_unit,
                ),
                origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
                configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
                source_property="Before",
            )
        )
    snapshot = SystemdDependencyUnitSnapshot(
        requested_name=artifact.dependent_unit,
        canonical_name=artifact.dependent_unit,
        names=(artifact.dependent_unit,),
        load_state="loaded",
        requires=(artifact.source_unit,)
        if selected_relation is DependencyRelation.REQUIRES
        else (),
        wants=(artifact.source_unit,)
        if selected_relation is DependencyRelation.WANTS
        else (),
        after=(artifact.source_unit,) if include_after else (),
        before=(artifact.source_unit,) if include_before else (),
        captured_at=observed_at,
    )
    source_event = SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
        message="Phase 5E.4 profiled dependency observation",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=observed_at,
        attributes={"observation_type": "linux.systemd.unit.dependency"},
    )
    unit = DiscoveredSystemdUnit(
        depth=0,
        snapshot=snapshot,
        source_event=source_event,
        evidence=tuple(evidence),
    )
    return SystemdDependencyDiscoveryReport(
        root_requested_unit=artifact.dependent_unit,
        root_canonical_unit=artifact.dependent_unit,
        boot_id=boot_id,
        max_depth=0,
        max_units=1,
        units=(unit,),
        failures=(),
        truncated_by_depth=True,
        unexpanded_requirement_count=1,
    )


def _candidate(artifact, *, boot_id: str = _BOOT):
    relation = artifact.spec.requirement_relation
    event_id = f"dep-event-{relation.value}-p5e4"
    observed_at = _TIME - timedelta(seconds=1)
    requirement = DependencyEvidence(
        evidence_id="depev-"
        + ("1" if relation is DependencyRelation.REQUIRES else "2") * 64,
        source_event_id=event_id,
        observed_at=observed_at,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=artifact.dependent_unit,
        ),
        relation=relation,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=artifact.source_unit,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property=(
            "Requires" if relation is DependencyRelation.REQUIRES else "Wants"
        ),
    )
    ordering = DependencyEvidence(
        evidence_id="depev-" + "3" * 64,
        source_event_id=event_id,
        observed_at=observed_at,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=artifact.dependent_unit,
        ),
        relation=DependencyRelation.AFTER,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=artifact.source_unit,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property="After",
    )
    snapshot = SystemdDependencyUnitSnapshot(
        requested_name=artifact.dependent_unit,
        canonical_name=artifact.dependent_unit,
        names=(artifact.dependent_unit,),
        load_state="loaded",
        requires=(artifact.source_unit,)
        if relation is DependencyRelation.REQUIRES
        else (),
        wants=(artifact.source_unit,) if relation is DependencyRelation.WANTS else (),
        after=(artifact.source_unit,),
        before=(),
        captured_at=observed_at,
    )
    source_event = SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
        message="Phase 5E.4 dependency observation",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=observed_at,
        attributes={"observation_type": "linux.systemd.unit.dependency"},
    )
    unit = DiscoveredSystemdUnit(
        depth=0,
        snapshot=snapshot,
        source_event=source_event,
        evidence=(requirement, ordering),
    )
    report = SystemdDependencyDiscoveryReport(
        root_requested_unit=artifact.dependent_unit,
        root_canonical_unit=artifact.dependent_unit,
        boot_id=boot_id,
        max_depth=0,
        max_units=1,
        units=(unit,),
        failures=(),
        truncated_by_depth=True,
        unexpanded_requirement_count=1,
    )
    candidates = build_dependency_propagation_candidates(build_dependency_graph(report))
    assert len(candidates) == 1
    return candidates[0]


def _assessment(
    unit: str,
    *,
    digit: str,
    assessed_usec: int,
    status: SystemdServiceHealthStatus,
    boot_id: str = _BOOT,
):
    if status is SystemdServiceHealthStatus.HEALTHY:
        active_state, sub_state, main_pid = "active", "running", 2222
        state_change_usec = _START_USEC - 200_000
    else:
        active_state, sub_state, main_pid = "inactive", "dead", None
        state_change_usec = _START_USEC
    event = SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.systemd.service",
        message=f"service observation for {unit}",
        severity=EventSeverity.INFO,
        event_id=f"evt-{digit}-{assessed_usec}",
        occurred_at=_TIME,
        attributes={
            "observation_type": "linux.systemd.service",
            "collector_name": "phase5e4_test",
            "boot_id": boot_id,
            "requested_name": unit,
            "canonical_name": unit,
            "load_state": "loaded",
            "active_state": active_state,
            "sub_state": sub_state,
            "main_pid": main_pid,
            "result": "success",
            "state_change_monotonic_usec": state_change_usec,
        },
    )
    assessment = SystemdServiceHealthAssessment(
        assessment_id="asmt-" + digit * 64,
        source_event_id=event.event_id,
        target_unit=unit,
        canonical_unit=unit,
        source_observed_at=event.occurred_at,
        assessed_at=event.occurred_at + timedelta(microseconds=assessed_usec),
        assessed_monotonic_usec=assessed_usec,
        state_change_monotonic_usec=state_change_usec,
        load_state="loaded",
        active_state=active_state,
        sub_state=sub_state,
        main_pid=main_pid,
        result="success",
        status=status,
        anomaly_class=None,
        severity=None,
        basis=DetectionBasis.DIRECT_SYSTEMD_STATE,
    )
    return bind_systemd_assessment_evidence(assessment, event)


def _live_run(
    artifact,
    manifest,
    policy,
    *,
    boot_id: str = _BOOT,
) -> ControlledPropagationLiveRun:
    candidate = _candidate(artifact, boot_id=boot_id)
    plan = SystemdLabFaultPlan(
        experiment_id=manifest.experiment_id,
        scenario_id=manifest.scenario.scenario_id,
        target_unit=artifact.source_unit,
        fault_mode=FaultMode.SERVICE_INACTIVE,
        operation=LabFaultOperation.STOP_SERVICE,
        expected_fault_active_states=("inactive",),
        recovery_operations=("start", "reset-failed"),
        artifact_sha256=artifact.source_artifact.sha256,
    )
    duration_usec = 800_000
    truth = FaultGroundTruthWindow(
        experiment_id=plan.experiment_id,
        scenario_id=plan.scenario_id,
        target_unit=artifact.source_unit,
        fault_mode=FaultMode.SERVICE_INACTIVE,
        started_at=_TIME,
        started_monotonic_usec=_START_USEC,
        ended_at=_TIME + timedelta(microseconds=duration_usec),
        ended_monotonic_usec=_START_USEC + duration_usec,
    )
    outcome = FaultInjectionOutcome(
        plan=plan,
        ground_truth=truth,
        baseline_state=SystemdLabServiceState(
            unit_name=artifact.source_unit,
            load_state="loaded",
            active_state="active",
            sub_state="running",
            main_pid=2001,
            result="success",
        ),
        fault_state=SystemdLabServiceState(
            unit_name=artifact.source_unit,
            load_state="loaded",
            active_state="inactive",
            sub_state="dead",
            main_pid=0,
            result="success",
        ),
        recovered_state=SystemdLabServiceState(
            unit_name=artifact.source_unit,
            load_state="loaded",
            active_state="active",
            sub_state="running",
            main_pid=2002,
            result="success",
        ),
    )
    points = (
        _START_USEC,
        _START_USEC + 200_000,
        _START_USEC + 400_000,
        _START_USEC + 600_000,
        _START_USEC + 800_000,
    )
    assessments = tuple(
        _assessment(
            artifact.dependent_unit,
            digit=digit,
            assessed_usec=usec,
            status=SystemdServiceHealthStatus.HEALTHY,
            boot_id=boot_id,
        )
        for digit, usec in zip(("1", "2", "3", "4", "5"), points, strict=True)
    )
    coverage = evaluate_controlled_ground_truth_coverage(
        candidate,
        truth,
        boot_id,
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
        boot_id_before=boot_id,
        boot_id_after=boot_id,
        post_recovery_verified=True,
    )


def _bundle():
    artifact = _artifact()
    manifest = _manifest(artifact)
    policy = _policy()
    live_run = _live_run(artifact, manifest, policy)
    return artifact, manifest, policy, live_run


def _capture_for(artifact, manifest, policy):
    return capture_controlled_propagation_protocol(artifact, manifest, policy)


def _attempt_for(artifact, manifest, policy):
    capture = _capture_for(artifact, manifest, policy)
    profile = build_controlled_propagation_execution_backend_profile()
    invoked_at = capture.captured_at + timedelta(seconds=1)
    invoked_usec = capture.captured_monotonic_usec + 100_000
    from sentinel_x.dependency import protocol_execution as module

    return module._build_attempt(
        protocol_capture=capture,
        backend_profile=profile,
        boot_id_before_capture=_BOOT,
        boot_id_after_capture=_BOOT,
        boot_id_before_invocation=_BOOT,
        effective_uid=0,
        effective_gid=0,
        invoked_at=invoked_at,
        invoked_monotonic_usec=invoked_usec,
    )


def _execution_for(artifact, manifest, policy, live_run):
    attempt = _attempt_for(artifact, manifest, policy)
    from sentinel_x.dependency import protocol_execution as module

    completed_at = attempt.live_runner_invoked_at + timedelta(seconds=1)
    completed_usec = attempt.live_runner_invoked_monotonic_usec + 1_000_000
    fingerprint = module._live_fingerprint(live_run)
    execution_id = module._execution_id(
        attempt_id=attempt.attempt_id,
        live_fingerprint=fingerprint,
        completed_at=completed_at,
        completed_monotonic_usec=completed_usec,
    )
    return ControlledPropagationProtocolBoundExecution(
        execution_id=execution_id,
        attempt=attempt,
        live_run=live_run,
        live_fingerprint=fingerprint,
        live_runner_completed_at=completed_at,
        live_runner_completed_monotonic_usec=completed_usec,
    )


class ControlledPropagationProtocolExecutionTests(unittest.TestCase):
    def test_default_backend_profile_is_deterministic_and_content_addressed(
        self,
    ) -> None:
        first = build_controlled_propagation_execution_backend_profile()
        second = build_controlled_propagation_execution_backend_profile()
        self.assertEqual(first, second)
        self.assertTrue(first.backend_profile_id.startswith("backendprof-"))

    def test_backend_profile_identity_changes_with_each_tunable_setting(self) -> None:
        baseline = build_controlled_propagation_execution_backend_profile()
        variants = (
            build_controlled_propagation_execution_backend_profile(
                service_systemctl_path="/opt/systemctl"
            ),
            build_controlled_propagation_execution_backend_profile(
                dependency_systemctl_path="/opt/systemctl"
            ),
            build_controlled_propagation_execution_backend_profile(
                lifecycle_systemctl_path="/opt/systemctl"
            ),
            build_controlled_propagation_execution_backend_profile(
                service_read_timeout_seconds=4.0
            ),
            build_controlled_propagation_execution_backend_profile(
                dependency_read_timeout_seconds=4.0
            ),
            build_controlled_propagation_execution_backend_profile(
                lifecycle_command_timeout_seconds=6.0
            ),
            build_controlled_propagation_execution_backend_profile(
                lifecycle_state_timeout_seconds=11.0
            ),
            build_controlled_propagation_execution_backend_profile(
                lifecycle_poll_interval_seconds=0.2
            ),
            build_controlled_propagation_execution_backend_profile(
                injector_command_timeout_seconds=6.0
            ),
            build_controlled_propagation_execution_backend_profile(
                injector_poll_interval_seconds=0.06
            ),
        )
        for variant in variants:
            self.assertNotEqual(baseline.backend_profile_id, variant.backend_profile_id)

    def test_backend_profile_rejects_nonabsolute_or_nul_paths(self) -> None:
        for value in ("systemctl", "", "/usr/bin/systemctl\x00bad"):
            with self.subTest(value=value):
                with self.assertRaises(
                    ControlledPropagationBoundExecutionContractError
                ):
                    build_controlled_propagation_execution_backend_profile(
                        service_systemctl_path=value
                    )

    def test_backend_profile_rejects_boolean_or_out_of_range_timeouts(self) -> None:
        for kwargs in (
            {"service_read_timeout_seconds": True},
            {"dependency_read_timeout_seconds": 0.01},
            {"lifecycle_command_timeout_seconds": 31.0},
            {"lifecycle_state_timeout_seconds": 61.0},
            {"lifecycle_poll_interval_seconds": 0.001},
            {"injector_command_timeout_seconds": 31.0},
            {"injector_poll_interval_seconds": 2.0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(
                    ControlledPropagationBoundExecutionContractError
                ):
                    build_controlled_propagation_execution_backend_profile(**kwargs)

    def test_backend_model_rejects_identity_drift(self) -> None:
        profile = build_controlled_propagation_execution_backend_profile()
        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            replace(profile, backend_profile_id="backendprof-" + "0" * 64)

    def test_frozen_injector_path_is_explicit_and_not_configurable(self) -> None:
        profile = build_controlled_propagation_execution_backend_profile()
        self.assertEqual(profile.injector_systemctl_path, "/usr/bin/systemctl")
        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            replace(profile, injector_systemctl_path="/opt/systemctl")

    def test_backend_serialization_scopes_full_configuration_claim(self) -> None:
        payload = build_controlled_propagation_execution_backend_profile().to_dict()
        self.assertEqual(
            payload["backend_scope"],
            ControlledPropagationExecutionBackendScope.SENTINEL_X_STANDARD_LIVE_BACKEND.value,
        )
        self.assertTrue(payload["execution_backend_configuration_fully_captured"])
        self.assertTrue(
            payload["execution_backend_configuration_scope_limited_to_sentinel_x"]
        )
        self.assertFalse(payload["host_environment_fully_captured"])
        self.assertFalse(payload["systemd_manager_version_captured"])
        self.assertFalse(payload["kernel_version_captured"])
        self.assertFalse(payload["executable_file_digest_captured"])

    def test_backend_serialization_preserves_no_shell_or_escalation_claims(
        self,
    ) -> None:
        payload = build_controlled_propagation_execution_backend_profile().to_dict()
        self.assertFalse(payload["subprocess_shell_invocation"])
        self.assertFalse(payload["privilege_escalation_performed_by_backend"])
        self.assertFalse(payload["unit_installation_performed_by_backend"])

    def test_backend_profile_records_fixed_clock_and_sleep_providers(self) -> None:
        payload = build_controlled_propagation_execution_backend_profile().to_dict()
        self.assertEqual(
            payload["service_detector_monotonic_provider"],
            "time.monotonic_ns",
        )
        self.assertEqual(payload["injector_monotonic_provider"], "time.monotonic")
        self.assertEqual(payload["injector_sleeper_provider"], "time.sleep")
        self.assertFalse(payload["executable_path_existence_verified_by_profile"])

    def test_standard_runner_builder_propagates_profile_configuration(self) -> None:
        from sentinel_x.dependency import protocol_execution as module

        profile = build_controlled_propagation_execution_backend_profile(
            service_systemctl_path="/opt/service-systemctl",
            dependency_systemctl_path="/opt/dependency-systemctl",
            lifecycle_systemctl_path="/opt/lifecycle-systemctl",
            service_read_timeout_seconds=4.0,
            dependency_read_timeout_seconds=5.0,
            lifecycle_command_timeout_seconds=6.0,
            lifecycle_state_timeout_seconds=12.0,
            lifecycle_poll_interval_seconds=0.2,
            injector_command_timeout_seconds=7.0,
            injector_poll_interval_seconds=0.06,
        )
        service_reader = object()
        lifecycle = object()
        injector = object()
        candidate_provider = object()
        sampler_factory = object()
        final_runner = object()
        with (
            patch.object(
                module,
                "SystemctlServiceReader",
                return_value=service_reader,
            ) as service_reader_cls,
            patch.object(
                module,
                "SystemdPropagationPairLifecycle",
                return_value=lifecycle,
            ) as lifecycle_cls,
            patch.object(
                module,
                "SystemdLabFaultInjector",
                return_value=injector,
            ) as injector_cls,
            patch.object(
                module,
                "_ProfiledCandidateProvider",
                return_value=candidate_provider,
            ) as candidate_cls,
            patch.object(
                module,
                "_ProfiledSamplerFactory",
                return_value=sampler_factory,
            ) as sampler_cls,
            patch.object(
                module,
                "ControlledPropagationLiveRunner",
                return_value=final_runner,
            ) as runner_cls,
        ):
            built = module._build_standard_live_runner(profile, expected_boot_id=_BOOT)

        self.assertIs(built, final_runner)
        service_reader_cls.assert_called_once_with(
            systemctl_path="/opt/service-systemctl",
            timeout_seconds=4.0,
        )
        lifecycle_cls.assert_called_once_with(
            reader=service_reader,
            systemctl_path="/opt/lifecycle-systemctl",
            command_timeout_seconds=6.0,
            state_timeout_seconds=12.0,
            poll_interval_seconds=0.2,
            pair_verifier=module.verify_installed_systemd_propagation_pair,
        )
        injector_cls.assert_called_once_with(
            command_timeout_seconds=7.0,
            poll_interval_seconds=0.06,
        )
        candidate_cls.assert_called_once_with(profile)
        sampler_cls.assert_called_once_with(profile)
        runner_cls.assert_called_once()
        _, runner_kwargs = runner_cls.call_args
        self.assertIs(
            runner_kwargs["pair_verifier"],
            module.verify_installed_systemd_propagation_pair,
        )
        self.assertIs(runner_kwargs["candidate_provider"], candidate_provider)
        self.assertIs(runner_kwargs["sampler_factory"], sampler_factory)
        self.assertIs(runner_kwargs["injector"], injector)
        self.assertIs(runner_kwargs["lifecycle"], lifecycle)
        bound_boot_reader = runner_kwargs["boot_id_reader"]
        with patch.object(module, "_read_normalized_boot_id", return_value=_BOOT):
            self.assertEqual(bound_boot_reader(), _BOOT)
        with patch.object(module, "_read_normalized_boot_id", return_value=_OTHER_BOOT):
            with self.assertRaises(
                ControlledPropagationBoundExecutionPreconditionError
            ):
                bound_boot_reader()

    def test_attempt_binds_capture_before_invocation(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        attempt = _attempt_for(artifact, manifest, policy)
        self.assertLessEqual(
            attempt.protocol_capture.captured_monotonic_usec,
            attempt.live_runner_invoked_monotonic_usec,
        )
        payload = attempt.to_dict()
        self.assertTrue(
            payload["capture_precedes_bound_live_runner_invocation_monotonic"]
        )
        self.assertTrue(
            payload["bound_runner_control_flow_orders_capture_before_mutation"]
        )

    def test_attempt_rejects_boot_change_across_capture(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        attempt = _attempt_for(artifact, manifest, policy)
        with self.assertRaises(ControlledPropagationBoundExecutionPreconditionError):
            replace(attempt, boot_id_after_capture=_OTHER_BOOT)

    def test_attempt_rejects_capture_after_invocation_monotonic_time(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        attempt = _attempt_for(artifact, manifest, policy)
        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            replace(
                attempt,
                live_runner_invoked_monotonic_usec=(
                    attempt.protocol_capture.captured_monotonic_usec - 1
                ),
            )

    def test_attempt_rejects_naive_invocation_wall_clock(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        attempt = _attempt_for(artifact, manifest, policy)
        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            replace(
                attempt,
                live_runner_invoked_at=attempt.live_runner_invoked_at.replace(
                    tzinfo=None
                ),
            )

    def test_attempt_rejects_invalid_privilege_context(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        attempt = _attempt_for(artifact, manifest, policy)
        for field_name, value in (("effective_uid", -1), ("effective_gid", True)):
            with self.subTest(field_name=field_name):
                with self.assertRaises(
                    ControlledPropagationBoundExecutionContractError
                ):
                    replace(attempt, **{field_name: value})

    def test_attempt_model_rejects_identity_drift(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        attempt = _attempt_for(artifact, manifest, policy)
        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            replace(attempt, attempt_id="protoattempt-" + "0" * 64)

    def test_attempt_serialization_does_not_claim_external_mutation_order(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        payload = _attempt_for(artifact, manifest, policy).to_dict()
        self.assertFalse(payload["capture_before_external_mutation_claim"])
        self.assertFalse(payload["capture_before_any_system_mutation_claim"])
        self.assertFalse(payload["least_privilege_claim_assigned"])
        self.assertFalse(payload["cryptographic_execution_attestation_assigned"])
        self.assertFalse(payload["content_addressed_identity_is_authentication"])
        self.assertFalse(payload["replication_claim_assigned"])
        self.assertFalse(payload["causal_claim"])
        self.assertEqual(payload["boot_id_before_invocation"], _BOOT)
        self.assertTrue(payload["boot_stable_through_pre_invocation"])
        self.assertEqual(
            payload["backend_scope"],
            ControlledPropagationExecutionBackendScope.SENTINEL_X_STANDARD_LIVE_BACKEND.value,
        )
        self.assertTrue(
            payload["execution_backend_configuration_scope_limited_to_sentinel_x"]
        )

    def test_execution_binds_exact_pair_policy_boot_and_manifest_identity(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        execution = _execution_for(artifact, manifest, policy, live_run)
        payload = execution.to_dict()
        self.assertTrue(payload["protocol_capture_matches_live_pair"])
        self.assertTrue(payload["protocol_capture_matches_live_policy"])
        self.assertTrue(
            payload["protocol_capture_matches_live_manifest_outcome_identity"]
        )
        self.assertTrue(payload["same_boot_bound_across_capture_and_live_run"])
        self.assertEqual(payload["boot_id_before_capture"], _BOOT)
        self.assertEqual(payload["boot_id_after_capture"], _BOOT)
        self.assertEqual(payload["boot_id_before_invocation"], _BOOT)

    def test_execution_rejects_live_run_from_different_pair(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        other_artifact = _artifact(DependencyRelation.WANTS)
        other_manifest = _manifest(
            other_artifact,
            digit="2",
            scenario_id="p5e4-wants-stop",
        )
        other_live = _live_run(other_artifact, other_manifest, policy)
        attempt = _attempt_for(artifact, manifest, policy)
        from sentinel_x.dependency import protocol_execution as module

        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            ControlledPropagationProtocolBoundExecution(
                execution_id="protoexec-" + "0" * 64,
                attempt=attempt,
                live_run=other_live,
                live_fingerprint=module._live_fingerprint(other_live),
                live_runner_completed_at=attempt.live_runner_invoked_at
                + timedelta(seconds=1),
                live_runner_completed_monotonic_usec=(
                    attempt.live_runner_invoked_monotonic_usec + 1_000_000
                ),
            )

    def test_execution_rejects_live_run_policy_drift(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        drift_policy = _policy(sample_interval_seconds=0.2)
        drift_live = _live_run(artifact, manifest, drift_policy)
        attempt = _attempt_for(artifact, manifest, policy)
        from sentinel_x.dependency import protocol_execution as module

        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            ControlledPropagationProtocolBoundExecution(
                execution_id="protoexec-" + "0" * 64,
                attempt=attempt,
                live_run=drift_live,
                live_fingerprint=module._live_fingerprint(drift_live),
                live_runner_completed_at=attempt.live_runner_invoked_at
                + timedelta(seconds=1),
                live_runner_completed_monotonic_usec=(
                    attempt.live_runner_invoked_monotonic_usec + 1_000_000
                ),
            )

    def test_execution_rejects_live_run_from_different_manifest_identity(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        other_manifest = _manifest(
            artifact,
            digit="2",
            scenario_id="p5e4-requires-repeat",
        )
        other_live = _live_run(artifact, other_manifest, policy)
        attempt = _attempt_for(artifact, manifest, policy)
        from sentinel_x.dependency import protocol_execution as module

        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            ControlledPropagationProtocolBoundExecution(
                execution_id="protoexec-" + "0" * 64,
                attempt=attempt,
                live_run=other_live,
                live_fingerprint=module._live_fingerprint(other_live),
                live_runner_completed_at=attempt.live_runner_invoked_at
                + timedelta(seconds=1),
                live_runner_completed_monotonic_usec=(
                    attempt.live_runner_invoked_monotonic_usec + 1_000_000
                ),
            )

    def test_execution_rejects_live_run_boot_drift(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        other_live = _live_run(artifact, manifest, policy, boot_id=_OTHER_BOOT)
        attempt = _attempt_for(artifact, manifest, policy)
        from sentinel_x.dependency import protocol_execution as module

        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            ControlledPropagationProtocolBoundExecution(
                execution_id="protoexec-" + "0" * 64,
                attempt=attempt,
                live_run=other_live,
                live_fingerprint=module._live_fingerprint(other_live),
                live_runner_completed_at=attempt.live_runner_invoked_at
                + timedelta(seconds=1),
                live_runner_completed_monotonic_usec=(
                    attempt.live_runner_invoked_monotonic_usec + 1_000_000
                ),
            )

    def test_execution_model_rejects_live_fingerprint_drift(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        execution = _execution_for(artifact, manifest, policy, live_run)
        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            replace(execution, live_fingerprint="livefp-" + "0" * 64)

    def test_execution_model_rejects_identity_drift(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        execution = _execution_for(artifact, manifest, policy, live_run)
        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            replace(execution, execution_id="protoexec-" + "0" * 64)

    def test_execution_rejects_completion_before_invocation(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        execution = _execution_for(artifact, manifest, policy, live_run)
        with self.assertRaises(ControlledPropagationBoundExecutionContractError):
            replace(
                execution,
                live_runner_completed_monotonic_usec=(
                    execution.attempt.live_runner_invoked_monotonic_usec - 1
                ),
            )

    def test_execution_serialization_is_bounded_and_omits_raw_sample_stream(
        self,
    ) -> None:
        artifact, manifest, policy, live_run = _bundle()
        payload = _execution_for(artifact, manifest, policy, live_run).to_dict()
        self.assertNotIn("dependent_assessments", payload)
        self.assertEqual(
            payload["dependent_assessment_count"],
            len(live_run.dependent_assessments),
        )

    def test_execution_serialization_preserves_claim_boundaries(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        payload = _execution_for(artifact, manifest, policy, live_run).to_dict()
        self.assertTrue(
            payload["capture_precedes_bound_live_runner_invocation_monotonic"]
        )
        self.assertTrue(
            payload[
                "bound_runner_control_flow_orders_capture_before_fault_ground_truth"
            ]
        )
        self.assertFalse(payload["host_environment_fully_captured"])
        self.assertFalse(payload["external_actor_state_captured"])
        self.assertFalse(payload["least_privilege_claim_assigned"])
        self.assertFalse(payload["cryptographic_execution_attestation_assigned"])
        self.assertFalse(payload["content_addressed_identity_is_authentication"])
        self.assertFalse(payload["replication_claim_assigned"])
        self.assertFalse(payload["statistical_significance_assigned"])
        self.assertFalse(payload["causal_effect_estimate_assigned"])
        self.assertFalse(payload["treatment_effect_claim_assigned"])
        self.assertFalse(payload["causal_claim"])
        self.assertFalse(payload["propagation_claim_assigned"])
        self.assertFalse(payload["root_cause_claim_assigned"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])
        self.assertFalse(payload["scalar_score_assigned"])

    def test_execution_schema_and_binding_scope_are_explicit(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        payload = _execution_for(artifact, manifest, policy, live_run).to_dict()
        self.assertEqual(
            payload["schema_version"],
            CONTROLLED_PROPAGATION_BOUND_EXECUTION_SCHEMA_VERSION,
        )
        self.assertEqual(
            payload["binding_scope"],
            ControlledPropagationExecutionBindingScope.BOUND_LIVE_RUN_INVOCATION.value,
        )

    def test_runner_rejects_untyped_inputs_before_capture(self) -> None:
        runner = ControlledPropagationProtocolBoundRunner()
        artifact, manifest, policy, _ = _bundle()
        for args in (
            (object(), manifest, policy),
            (artifact, object(), policy),
            (artifact, manifest, object()),
        ):
            with self.subTest(args=args):
                with self.assertRaises(
                    ControlledPropagationBoundExecutionContractError
                ):
                    runner.run(*args)

    def test_bound_boot_reader_blocks_frozen_lifecycle_before_mutation_on_race(
        self,
    ) -> None:
        from sentinel_x.dependency import protocol_execution as module

        artifact, manifest, policy, _ = _bundle()
        lifecycle = Mock()
        injector = Mock()
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda _artifact: None,
            boot_id_reader=lambda: module._read_bound_boot_id(_BOOT),
            candidate_provider=Mock(),
            sampler_factory=Mock(),
            injector=injector,
            lifecycle=lifecycle,
        )
        with patch.object(module, "_read_normalized_boot_id", return_value=_OTHER_BOOT):
            with self.assertRaises(
                ControlledPropagationBoundExecutionPreconditionError
            ):
                runner.run(artifact, manifest, policy)
        lifecycle.prepare.assert_not_called()
        injector.execute.assert_not_called()

    def test_runner_rejects_boot_change_during_protocol_capture_before_live_runner(
        self,
    ) -> None:
        artifact, manifest, policy, _ = _bundle()
        runner = ControlledPropagationProtocolBoundRunner()
        with (
            patch(
                "sentinel_x.dependency.protocol_execution._read_normalized_boot_id",
                side_effect=(_BOOT, _OTHER_BOOT),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution._build_standard_live_runner"
            ) as build_runner,
        ):
            with self.assertRaises(
                ControlledPropagationBoundExecutionPreconditionError
            ):
                runner.run(artifact, manifest, policy)
        build_runner.assert_not_called()

    def test_runner_rejects_boot_change_after_capture_before_live_runner_invocation(
        self,
    ) -> None:
        artifact, manifest, policy, _ = _bundle()
        capture = _capture_for(artifact, manifest, policy)
        runner = ControlledPropagationProtocolBoundRunner()
        fake_runner = Mock()
        with (
            patch(
                "sentinel_x.dependency.protocol_execution._read_normalized_boot_id",
                side_effect=(_BOOT, _BOOT, _OTHER_BOOT),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.capture_controlled_propagation_protocol",
                return_value=capture,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution._build_standard_live_runner",
                return_value=fake_runner,
            ),
        ):
            with self.assertRaises(
                ControlledPropagationBoundExecutionPreconditionError
            ):
                runner.run(artifact, manifest, policy)
        fake_runner.run.assert_not_called()

    def test_runner_orders_capture_before_live_runner_invocation(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        events: list[str] = []
        capture = _capture_for(artifact, manifest, policy)
        fake_runner = Mock()

        def fake_run(*_args):
            events.append("live_run")
            return live_run

        fake_runner.run.side_effect = fake_run

        def fake_capture(*_args):
            events.append("capture")
            return capture

        with (
            patch(
                "sentinel_x.dependency.protocol_execution._read_normalized_boot_id",
                side_effect=(_BOOT, _BOOT, _BOOT),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.capture_controlled_propagation_protocol",
                side_effect=fake_capture,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution._build_standard_live_runner",
                return_value=fake_runner,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.time.monotonic_ns",
                side_effect=(
                    (capture.captured_monotonic_usec + 100_000) * 1_000,
                    (capture.captured_monotonic_usec + 1_100_000) * 1_000,
                ),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.os.geteuid",
                return_value=0,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.os.getegid",
                return_value=0,
            ),
        ):
            result = ControlledPropagationProtocolBoundRunner().run(
                artifact,
                manifest,
                policy,
            )

        self.assertEqual(events, ["capture", "live_run"])
        self.assertEqual(result.live_run, live_run)

    def test_runner_failure_preserves_bound_attempt(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        capture = _capture_for(artifact, manifest, policy)
        fake_runner = Mock()
        fake_runner.run.side_effect = RuntimeError("synthetic failure")

        with (
            patch(
                "sentinel_x.dependency.protocol_execution._read_normalized_boot_id",
                side_effect=(_BOOT, _BOOT, _BOOT),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.capture_controlled_propagation_protocol",
                return_value=capture,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution._build_standard_live_runner",
                return_value=fake_runner,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.time.monotonic_ns",
                return_value=(capture.captured_monotonic_usec + 100_000) * 1_000,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.os.geteuid",
                return_value=0,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.os.getegid",
                return_value=0,
            ),
        ):
            with self.assertRaises(
                ControlledPropagationBoundExecutionRunError
            ) as caught:
                ControlledPropagationProtocolBoundRunner().run(
                    artifact,
                    manifest,
                    policy,
                )

        self.assertEqual(caught.exception.attempt.protocol_capture, capture)
        self.assertEqual(caught.exception.cause_type, "RuntimeError")

    def test_post_run_binding_failure_preserves_bound_attempt(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        capture = _capture_for(artifact, manifest, policy)
        other_artifact = _artifact(DependencyRelation.WANTS)
        other_manifest = _manifest(
            other_artifact, digit="2", scenario_id="p5e4-wants-stop"
        )
        mismatched_live_run = _live_run(other_artifact, other_manifest, policy)
        fake_runner = Mock()
        fake_runner.run.return_value = mismatched_live_run

        with (
            patch(
                "sentinel_x.dependency.protocol_execution._read_normalized_boot_id",
                side_effect=(_BOOT, _BOOT, _BOOT),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.capture_controlled_propagation_protocol",
                return_value=capture,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution._build_standard_live_runner",
                return_value=fake_runner,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.time.monotonic_ns",
                side_effect=(
                    (capture.captured_monotonic_usec + 100_000) * 1_000,
                    (capture.captured_monotonic_usec + 1_100_000) * 1_000,
                ),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.os.geteuid", return_value=0
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.os.getegid", return_value=0
            ),
        ):
            with self.assertRaises(
                ControlledPropagationBoundExecutionRunError
            ) as caught:
                ControlledPropagationProtocolBoundRunner().run(
                    artifact, manifest, policy
                )

        self.assertEqual(caught.exception.attempt.protocol_capture, capture)
        self.assertEqual(
            caught.exception.cause_type,
            "ControlledPropagationBoundExecutionContractError",
        )

    def test_runner_does_not_wrap_keyboard_interrupt_or_system_exit(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        capture = _capture_for(artifact, manifest, policy)
        for failure in (KeyboardInterrupt(), SystemExit(2)):
            fake_runner = Mock()
            fake_runner.run.side_effect = failure
            with (
                self.subTest(failure=type(failure).__name__),
                patch(
                    "sentinel_x.dependency.protocol_execution._read_normalized_boot_id",
                    side_effect=(_BOOT, _BOOT, _BOOT),
                ),
                patch(
                    "sentinel_x.dependency.protocol_execution.capture_controlled_propagation_protocol",
                    return_value=capture,
                ),
                patch(
                    "sentinel_x.dependency.protocol_execution._build_standard_live_runner",
                    return_value=fake_runner,
                ),
                patch(
                    "sentinel_x.dependency.protocol_execution.time.monotonic_ns",
                    return_value=(capture.captured_monotonic_usec + 100_000) * 1_000,
                ),
                patch(
                    "sentinel_x.dependency.protocol_execution.os.geteuid",
                    return_value=0,
                ),
                patch(
                    "sentinel_x.dependency.protocol_execution.os.getegid",
                    return_value=0,
                ),
            ):
                with self.assertRaises(type(failure)):
                    ControlledPropagationProtocolBoundRunner().run(
                        artifact,
                        manifest,
                        policy,
                    )

    def test_runner_binds_privilege_context_without_least_privilege_claim(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        capture = _capture_for(artifact, manifest, policy)
        fake_runner = Mock()
        fake_runner.run.return_value = live_run
        with (
            patch(
                "sentinel_x.dependency.protocol_execution._read_normalized_boot_id",
                side_effect=(_BOOT, _BOOT, _BOOT),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.capture_controlled_propagation_protocol",
                return_value=capture,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution._build_standard_live_runner",
                return_value=fake_runner,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.time.monotonic_ns",
                side_effect=(
                    (capture.captured_monotonic_usec + 100_000) * 1_000,
                    (capture.captured_monotonic_usec + 1_100_000) * 1_000,
                ),
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.os.geteuid",
                return_value=123,
            ),
            patch(
                "sentinel_x.dependency.protocol_execution.os.getegid",
                return_value=456,
            ),
        ):
            result = ControlledPropagationProtocolBoundRunner().run(
                artifact,
                manifest,
                policy,
            )
        self.assertEqual(result.attempt.effective_uid, 123)
        self.assertEqual(result.attempt.effective_gid, 456)
        self.assertFalse(result.to_dict()["least_privilege_claim_assigned"])

    def test_profile_constructor_rejects_untyped_profile(self) -> None:
        with self.assertRaises(TypeError):
            ControlledPropagationProtocolBoundRunner(backend_profile=object())

    def test_protocol_capture_itself_remains_preexecution_and_nonbinding(self) -> None:
        artifact, manifest, policy, _ = _bundle()
        capture = _capture_for(artifact, manifest, policy)
        payload = capture.to_dict()
        self.assertFalse(payload["execution_backend_binding_assigned"])
        self.assertFalse(payload["capture_before_live_runner_invocation_claim"])
        self.assertFalse(payload["same_boot_claim_assigned"])

    def test_live_fingerprint_changes_with_distinct_valid_live_record(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        from sentinel_x.dependency import protocol_execution as module

        other_manifest = _manifest(
            artifact,
            digit="2",
            scenario_id="p5e4-requires-repeat",
        )
        other_live = _live_run(artifact, other_manifest, policy)
        self.assertNotEqual(
            module._live_fingerprint(live_run),
            module._live_fingerprint(other_live),
        )

    def test_backend_profile_serialization_has_fixed_candidate_bounds(self) -> None:
        payload = build_controlled_propagation_execution_backend_profile().to_dict()
        self.assertEqual(payload["candidate_max_depth"], 0)
        self.assertEqual(payload["candidate_max_units"], 1)

    def test_execution_does_not_assign_experiment_wide_single_variable_isolation(
        self,
    ) -> None:
        artifact, manifest, policy, live_run = _bundle()
        payload = _execution_for(artifact, manifest, policy, live_run).to_dict()
        self.assertFalse(payload["experiment_wide_single_variable_isolation_claim"])

    def test_execution_does_not_generalize_beyond_bound_run(self) -> None:
        artifact, manifest, policy, live_run = _bundle()
        payload = _execution_for(artifact, manifest, policy, live_run).to_dict()
        self.assertFalse(payload["generalization_beyond_bound_execution_permitted"])
        self.assertFalse(payload["universal_systemd_behavior_claim"])

    def test_profiled_candidate_provider_accepts_exact_manager_relation_and_after(
        self,
    ) -> None:
        from sentinel_x.dependency import protocol_execution as module

        artifact = _artifact()
        profile = build_controlled_propagation_execution_backend_profile(
            dependency_systemctl_path="/opt/dependency-systemctl",
            dependency_read_timeout_seconds=4.0,
        )
        report = _report_for(artifact)
        discovery = Mock()
        discovery.discover.return_value = report
        with (
            patch.object(
                module,
                "SystemctlDependencyUnitReader",
                return_value=object(),
            ) as reader_cls,
            patch.object(
                module,
                "SystemdDependencyDiscovery",
                return_value=discovery,
            ) as discovery_cls,
        ):
            candidate = module._ProfiledCandidateProvider(profile)(artifact, _BOOT)

        self.assertEqual(candidate.dependency_unit, artifact.source_unit)
        self.assertEqual(candidate.dependent_unit, artifact.dependent_unit)
        self.assertIs(candidate.requirement_relation, DependencyRelation.REQUIRES)
        reader_cls.assert_called_once_with(
            systemctl_path="/opt/dependency-systemctl",
            timeout_seconds=4.0,
        )
        discovery_cls.assert_called_once()
        _, kwargs = discovery_cls.call_args
        self.assertEqual(kwargs["max_depth"], 0)
        self.assertEqual(kwargs["max_units"], 1)

    def test_profiled_candidate_provider_rejects_relation_drift(self) -> None:
        from sentinel_x.dependency import protocol_execution as module

        artifact = _artifact(DependencyRelation.REQUIRES)
        report = _report_for(artifact, relation=DependencyRelation.WANTS)
        discovery = Mock()
        discovery.discover.return_value = report
        with (
            patch.object(
                module,
                "SystemctlDependencyUnitReader",
                return_value=object(),
            ),
            patch.object(
                module,
                "SystemdDependencyDiscovery",
                return_value=discovery,
            ),
        ):
            with self.assertRaises(module.ControlledPropagationLivePreconditionError):
                module._ProfiledCandidateProvider(
                    build_controlled_propagation_execution_backend_profile()
                )(artifact, _BOOT)

    def test_profiled_candidate_provider_rejects_missing_after_edge(self) -> None:
        from sentinel_x.dependency import protocol_execution as module

        artifact = _artifact()
        report = _report_for(artifact, include_after=False)
        discovery = Mock()
        discovery.discover.return_value = report
        with (
            patch.object(
                module,
                "SystemctlDependencyUnitReader",
                return_value=object(),
            ),
            patch.object(
                module,
                "SystemdDependencyDiscovery",
                return_value=discovery,
            ),
        ):
            with self.assertRaises(module.ControlledPropagationLivePreconditionError):
                module._ProfiledCandidateProvider(
                    build_controlled_propagation_execution_backend_profile()
                )(artifact, _BOOT)

    def test_profiled_candidate_provider_rejects_unexpected_pair_ordering_context(
        self,
    ) -> None:
        from sentinel_x.dependency import protocol_execution as module

        artifact = _artifact()
        profile = build_controlled_propagation_execution_backend_profile()
        report = _report_for(artifact, include_before=True)
        discovery = Mock()
        discovery.discover.return_value = report
        with (
            patch.object(
                module, "SystemctlDependencyUnitReader", return_value=object()
            ),
            patch.object(module, "SystemdDependencyDiscovery", return_value=discovery),
        ):
            with self.assertRaises(ControlledPropagationLivePreconditionError):
                module._ProfiledCandidateProvider(profile)(artifact, _BOOT)

    def test_profiled_sampler_factory_uses_profiled_reader_and_standard_detector(
        self,
    ) -> None:
        from sentinel_x.dependency import protocol_execution as module

        profile = build_controlled_propagation_execution_backend_profile(
            service_systemctl_path="/opt/service-systemctl",
            service_read_timeout_seconds=4.0,
        )
        reader = object()
        detector = object()
        sampler = object()
        with (
            patch.object(
                module,
                "SystemctlServiceReader",
                return_value=reader,
            ) as reader_cls,
            patch.object(
                module,
                "SystemdServiceStateDetector",
                return_value=detector,
            ) as detector_cls,
            patch.object(
                module,
                "SystemdDependentAssessmentSampler",
                return_value=sampler,
            ) as sampler_cls,
        ):
            result = module._ProfiledSamplerFactory(profile)(
                "sentinel-x-lab-p5e4-dependent.service"
            )

        self.assertIs(result, sampler)
        reader_cls.assert_called_once_with(
            systemctl_path="/opt/service-systemctl",
            timeout_seconds=4.0,
        )
        detector_cls.assert_called_once_with()
        sampler_cls.assert_called_once_with(
            "sentinel-x-lab-p5e4-dependent.service",
            reader=reader,
            detector=detector,
            boot_id_reader=module.read_current_boot_id,
        )

    def test_backend_profile_is_publicly_typed(self) -> None:
        profile = build_controlled_propagation_execution_backend_profile()
        self.assertIsInstance(profile, ControlledPropagationExecutionBackendProfile)


if __name__ == "__main__":
    unittest.main()
