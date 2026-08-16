from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

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
    SystemdPropagationPairArtifact,
    SystemdPropagationPairSpec,
    build_controlled_propagation_experiment_record,
    build_systemd_propagation_pair,
    evaluate_controlled_ground_truth_coverage,
)
from sentinel_x.dependency.propagation_live import (
    ControlledPropagationLiveCapacityError,
    ControlledPropagationLiveContractError,
    ControlledPropagationLivePolicy,
    ControlledPropagationLivePreconditionError,
    ControlledPropagationLiveRecoveryError,
    ControlledPropagationLiveRunner,
    ControlledPropagationLiveSamplingError,
    ControlledPropagationLiveRun,
    SystemdPropagationPairLifecycle,
    _DependentAssessmentCapture,
    _LifecycleCommandResult,
)
from sentinel_x.detection.models import (
    DetectionAnomalyClass,
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
_TIME = datetime(2026, 8, 15, 20, 0, tzinfo=timezone.utc)
_SOURCE_ID = "p53-source"
_DEPENDENT_ID = "p53-dependent"
_SOURCE = f"sentinel-x-lab-{_SOURCE_ID}.service"
_DEPENDENT = f"sentinel-x-lab-{_DEPENDENT_ID}.service"


def _pair_spec(
    relation: DependencyRelation = DependencyRelation.REQUIRES,
) -> SystemdPropagationPairSpec:
    return SystemdPropagationPairSpec(
        source=SystemdLabFixtureSpec(_SOURCE_ID, runtime_max_seconds=120),
        dependent=SystemdLabFixtureSpec(_DEPENDENT_ID, runtime_max_seconds=120),
        requirement_relation=relation,
    )


def _candidate(
    relation: DependencyRelation = DependencyRelation.REQUIRES,
    *,
    boot_id: str = _BOOT,
):
    graph_time = _TIME - timedelta(seconds=1)
    event_id = "dep-event"
    evidence = DependencyEvidence(
        evidence_id="depev-" + "1" * 64,
        source_event_id=event_id,
        observed_at=graph_time,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=_DEPENDENT,
        ),
        relation=relation,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=_SOURCE,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property="Requires"
        if relation is DependencyRelation.REQUIRES
        else "Wants",
    )
    ordering = DependencyEvidence(
        evidence_id="depev-" + "2" * 64,
        source_event_id=event_id,
        observed_at=graph_time,
        boot_id=boot_id,
        subject=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=_DEPENDENT,
        ),
        relation=DependencyRelation.AFTER,
        object=DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=_SOURCE,
        ),
        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
        configuration_origin=DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED,
        source_property="After",
    )
    snapshot = SystemdDependencyUnitSnapshot(
        requested_name=_DEPENDENT,
        canonical_name=_DEPENDENT,
        names=(_DEPENDENT,),
        load_state="loaded",
        requires=(_SOURCE,) if relation is DependencyRelation.REQUIRES else (),
        wants=(_SOURCE,) if relation is DependencyRelation.WANTS else (),
        after=(_SOURCE,),
        before=(),
        captured_at=graph_time,
    )
    source_event = SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
        message=f"dependency observation for {_DEPENDENT}",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=graph_time,
        attributes={"observation_type": "linux.systemd.unit.dependency"},
    )
    unit = DiscoveredSystemdUnit(
        depth=0,
        snapshot=snapshot,
        source_event=source_event,
        evidence=(evidence, ordering),
    )
    report = SystemdDependencyDiscoveryReport(
        root_requested_unit=_DEPENDENT,
        root_canonical_unit=_DEPENDENT,
        boot_id=boot_id,
        max_depth=1,
        max_units=64,
        units=(unit,),
        failures=(),
        truncated_by_depth=True,
        unexpanded_requirement_count=1,
    )
    return build_dependency_propagation_candidates(build_dependency_graph(report))[0]


def _event(
    unit: str,
    *,
    event_id: str,
    assessed_status: SystemdServiceHealthStatus,
    state_change_usec: int | None,
    boot_id: str = _BOOT,
) -> SentinelEvent:
    if assessed_status is SystemdServiceHealthStatus.HEALTHY:
        active_state, sub_state, main_pid, result = "active", "running", 2222, "success"
    elif assessed_status is SystemdServiceHealthStatus.INACTIVE:
        active_state, sub_state, main_pid, result = "inactive", "dead", None, "success"
    elif assessed_status is SystemdServiceHealthStatus.FAILED:
        active_state, sub_state, main_pid, result = (
            "failed",
            "failed",
            None,
            "exit-code",
        )
    else:
        active_state, sub_state, main_pid, result = "activating", "start", None, None
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.systemd.service",
        message=f"systemd service observation for {unit}",
        severity=EventSeverity.INFO,
        event_id=event_id,
        occurred_at=_TIME,
        attributes={
            "observation_type": "linux.systemd.service",
            "collector_name": "phase5d3_test",
            "boot_id": boot_id,
            "requested_name": unit,
            "canonical_name": unit,
            "load_state": "loaded",
            "active_state": active_state,
            "sub_state": sub_state,
            "main_pid": main_pid,
            "result": result,
            "state_change_monotonic_usec": state_change_usec,
        },
    )


def _assessment_evidence(
    unit: str,
    *,
    digit: str,
    assessed_usec: int,
    state_change_usec: int | None,
    status: SystemdServiceHealthStatus,
    boot_id: str = _BOOT,
):
    event = _event(
        unit,
        event_id=f"event-{digit}",
        assessed_status=status,
        state_change_usec=state_change_usec,
        boot_id=boot_id,
    )
    if status is SystemdServiceHealthStatus.HEALTHY:
        anomaly_class = None
        severity = None
        active_state, sub_state, main_pid, result = "active", "running", 2222, "success"
    elif status is SystemdServiceHealthStatus.INACTIVE:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_INACTIVE
        severity = EventSeverity.WARNING
        active_state, sub_state, main_pid, result = "inactive", "dead", None, "success"
    elif status is SystemdServiceHealthStatus.FAILED:
        anomaly_class = DetectionAnomalyClass.SYSTEMD_SERVICE_FAILED
        severity = EventSeverity.ERROR
        active_state, sub_state, main_pid, result = (
            "failed",
            "failed",
            None,
            "exit-code",
        )
    else:
        anomaly_class = None
        severity = None
        active_state, sub_state, main_pid, result = "activating", "start", None, None
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
        result=result,
        status=status,
        anomaly_class=anomaly_class,
        severity=severity,
        basis=DetectionBasis.DIRECT_SYSTEMD_STATE,
    )
    return bind_systemd_assessment_evidence(assessment, event)


def _outcome(
    artifact: SystemdPropagationPairArtifact,
    *,
    mode: FaultMode = FaultMode.SERVICE_INACTIVE,
) -> FaultInjectionOutcome:
    operation = (
        LabFaultOperation.STOP_SERVICE
        if mode is FaultMode.SERVICE_INACTIVE
        else LabFaultOperation.ABORT_MAIN_PROCESS
    )
    plan = SystemdLabFaultPlan(
        experiment_id="exp-" + "1" * 32,
        scenario_id="phase5d3-case",
        target_unit=artifact.source_unit,
        fault_mode=mode,
        operation=operation,
        expected_fault_active_states=("inactive",)
        if mode is FaultMode.SERVICE_INACTIVE
        else ("failed",),
        recovery_operations=("start", "reset-failed"),
        artifact_sha256=artifact.source_artifact.sha256,
    )
    ground_truth = FaultGroundTruthWindow(
        experiment_id=plan.experiment_id,
        scenario_id=plan.scenario_id,
        target_unit=artifact.source_unit,
        fault_mode=mode,
        started_at=_TIME,
        started_monotonic_usec=1_100_000,
        ended_at=_TIME + timedelta(seconds=1),
        ended_monotonic_usec=2_100_000,
    )
    healthy = SystemdLabServiceState(
        unit_name=artifact.source_unit,
        load_state="loaded",
        active_state="active",
        sub_state="running",
        main_pid=2222,
        result="success",
    )
    fault = SystemdLabServiceState(
        unit_name=artifact.source_unit,
        load_state="loaded",
        active_state="inactive" if mode is FaultMode.SERVICE_INACTIVE else "failed",
        sub_state="dead" if mode is FaultMode.SERVICE_INACTIVE else "failed",
        main_pid=0,
        result="success" if mode is FaultMode.SERVICE_INACTIVE else "signal",
    )
    return FaultInjectionOutcome(
        plan=plan,
        ground_truth=ground_truth,
        baseline_state=healthy,
        fault_state=fault,
        recovered_state=healthy,
    )


def _manifest(artifact: SystemdPropagationPairArtifact) -> FaultExperimentManifest:
    return FaultExperimentManifest(
        scenario=FaultScenario(
            scenario_id="phase5d3-case",
            description="Controlled explicit source deactivation for paired propagation evidence.",
            target_unit=artifact.source_unit,
            fault_mode=FaultMode.SERVICE_INACTIVE,
            baseline_timeout_seconds=1.0,
            fault_timeout_seconds=1.0,
            recovery_timeout_seconds=2.0,
            evidence_grace_seconds=0.1,
        ),
        experiment_id="exp-" + "1" * 32,
        created_at=_TIME - timedelta(seconds=2),
    )


class _SequenceSampler:
    def __init__(
        self,
        unit: str,
        *,
        boot_id: str = _BOOT,
        statuses: tuple[SystemdServiceHealthStatus, ...] = (
            SystemdServiceHealthStatus.HEALTHY,
        ),
    ) -> None:
        self.unit = unit
        self.boot_id = boot_id
        self.statuses = statuses
        self.calls = 0

    def __call__(self):
        index = self.calls
        self.calls += 1
        status = self.statuses[min(index, len(self.statuses) - 1)]
        digit = "123456789abcdef"[index % 15]
        assessed_usec = 1_000_000 + index * 100_000
        state_change_usec = (
            800_000
            if status is SystemdServiceHealthStatus.HEALTHY
            else min(assessed_usec, 1_100_000)
        )
        return _assessment_evidence(
            self.unit,
            digit=digit,
            assessed_usec=assessed_usec,
            state_change_usec=state_change_usec,
            status=status,
            boot_id=self.boot_id,
        )


class _FakeLifecycle:
    def __init__(self, *, prepare_error=None, recover_error=None) -> None:
        self.prepare_error = prepare_error
        self.recover_error = recover_error
        self.prepare_calls = 0
        self.recover_calls = 0

    def prepare(self, artifact) -> None:
        self.prepare_calls += 1
        if self.prepare_error is not None:
            raise self.prepare_error

    def recover(self, artifact) -> None:
        self.recover_calls += 1
        if self.recover_error is not None:
            raise self.recover_error


class _FakeInjector:
    def __init__(self, outcome, *, error=None, delay=0.0) -> None:
        self.outcome = outcome
        self.error = error
        self.delay = delay
        self.calls = 0

    def execute(self, manifest, artifact):
        self.calls += 1
        if self.delay:
            import time

            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.outcome


class ControlledPropagationLiveTests(unittest.TestCase):
    def test_lifecycle_prepare_starts_fresh_pair_before_reset_failed(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        loaded_units: set[str] = set()
        commands: list[tuple[str, ...]] = []

        class _FreshReader:
            def read_service(self, unit_name: str):
                if unit_name not in loaded_units:
                    raise AssertionError("health read occurred before pair activation")
                return type(
                    "_Snapshot",
                    (),
                    {
                        "load_state": "loaded",
                        "active_state": "active",
                        "sub_state": "running",
                        "main_pid": 1234,
                    },
                )()

        def _runner(argv, *, timeout_seconds):
            del timeout_seconds
            command = tuple(argv[4:])
            commands.append(command)
            if command[0] == "start":
                loaded_units.update((artifact.source_unit, artifact.dependent_unit))
                return _LifecycleCommandResult(returncode=0, stdout=b"", stderr=b"")
            if command[0] == "reset-failed":
                targets = command[2:]
                if any(unit_name not in loaded_units for unit_name in targets):
                    return _LifecycleCommandResult(
                        returncode=1,
                        stdout=b"",
                        stderr=b"Unit not loaded",
                    )
                return _LifecycleCommandResult(returncode=0, stdout=b"", stderr=b"")
            raise AssertionError(f"unexpected command: {command}")

        lifecycle = SystemdPropagationPairLifecycle(
            reader=_FreshReader(),
            runner=_runner,
            sleeper=lambda _: None,
            systemctl_path="/usr/bin/systemctl",
            pair_verifier=lambda artifact: None,
        )
        lifecycle.prepare(artifact)

        self.assertEqual(
            commands,
            [
                ("start", "--", artifact.dependent_unit),
                (
                    "reset-failed",
                    "--",
                    artifact.source_unit,
                    artifact.dependent_unit,
                ),
            ],
        )

    def test_policy_is_typed_and_serializes_caller_gap_without_confidence(self) -> None:
        policy = ControlledPropagationLivePolicy(max_sample_gap_usec=250_000)
        payload = policy.to_dict()
        self.assertTrue(payload["max_sample_gap_declared_by_caller"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])
        self.assertEqual(payload["max_sample_gap_usec"], 250_000)

    def test_policy_rejects_boolean_zero_and_out_of_bounds_values(self) -> None:
        for value in (True, 0, -1, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ControlledPropagationLiveContractError):
                    ControlledPropagationLivePolicy(max_sample_gap_usec=value)  # type: ignore[arg-type]
        with self.assertRaises(ControlledPropagationLiveContractError):
            ControlledPropagationLivePolicy(max_sample_gap_usec=1, max_samples=1)
        with self.assertRaises(ControlledPropagationLiveContractError):
            ControlledPropagationLivePolicy(
                max_sample_gap_usec=1, sample_interval_seconds=0.001
            )

    def test_capture_requires_healthy_first_sample(self) -> None:
        capture = _DependentAssessmentCapture(
            sampler=_SequenceSampler(
                _DEPENDENT,
                statuses=(SystemdServiceHealthStatus.INACTIVE,),
            ),
            dependent_unit=_DEPENDENT,
            expected_boot_id=_BOOT,
            sample_interval_seconds=0.02,
            max_samples=8,
            join_timeout_seconds=1.0,
        )
        with self.assertRaises(ControlledPropagationLivePreconditionError):
            capture.start()

    def test_capture_rejects_cross_boot_sample(self) -> None:
        capture = _DependentAssessmentCapture(
            sampler=_SequenceSampler(_DEPENDENT, boot_id="b" * 32),
            dependent_unit=_DEPENDENT,
            expected_boot_id=_BOOT,
            sample_interval_seconds=0.02,
            max_samples=8,
            join_timeout_seconds=1.0,
        )
        with self.assertRaises(ControlledPropagationLiveSamplingError):
            capture.start()

    def test_capture_rejects_wrong_unit_sample(self) -> None:
        capture = _DependentAssessmentCapture(
            sampler=_SequenceSampler(_SOURCE),
            dependent_unit=_DEPENDENT,
            expected_boot_id=_BOOT,
            sample_interval_seconds=0.02,
            max_samples=8,
            join_timeout_seconds=1.0,
        )
        with self.assertRaises(ControlledPropagationLiveSamplingError):
            capture.start()

    def test_capture_capacity_fails_explicitly_without_eviction(self) -> None:
        sampler = _SequenceSampler(_DEPENDENT)
        capture = _DependentAssessmentCapture(
            sampler=sampler,
            dependent_unit=_DEPENDENT,
            expected_boot_id=_BOOT,
            sample_interval_seconds=0.02,
            max_samples=2,
            join_timeout_seconds=1.0,
        )
        capture.start()
        import time

        time.sleep(0.06)
        with self.assertRaises(ControlledPropagationLiveSamplingError) as ctx:
            capture.finish()
        self.assertIsInstance(
            ctx.exception.__cause__, ControlledPropagationLiveCapacityError
        )

    def test_runner_rejects_noninactive_manifest_before_any_mutation(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        bad = FaultExperimentManifest(
            scenario=replace(
                _manifest(artifact).scenario, fault_mode=FaultMode.SERVICE_FAILED
            ),
            experiment_id="exp-" + "2" * 32,
            created_at=_TIME,
        )
        lifecycle = _FakeLifecycle()
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(
                artifact.spec.requirement_relation
            ),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(_outcome(artifact)),
            lifecycle=lifecycle,
        )
        with self.assertRaises(ControlledPropagationLiveContractError):
            runner.run(
                artifact,
                bad,
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )
        self.assertEqual(lifecycle.prepare_calls, 0)

    def test_runner_requires_manifest_target_to_be_pair_source(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        manifest = _manifest(artifact)
        bad = FaultExperimentManifest(
            scenario=replace(manifest.scenario, target_unit=artifact.dependent_unit),
            experiment_id=manifest.experiment_id,
            created_at=manifest.created_at,
        )
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(_outcome(artifact)),
            lifecycle=_FakeLifecycle(),
        )
        with self.assertRaises(ControlledPropagationLiveContractError):
            runner.run(
                artifact,
                bad,
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )

    def test_runner_rejects_boot_change_after_prepare_before_fault(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        boot_values = iter((_BOOT, "b" * 32))
        lifecycle = _FakeLifecycle()
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: next(boot_values),
            candidate_provider=lambda artifact, boot: _candidate(),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(_outcome(artifact)),
            lifecycle=lifecycle,
        )
        with self.assertRaises(ControlledPropagationLivePreconditionError):
            runner.run(
                artifact,
                _manifest(artifact),
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )
        self.assertEqual(lifecycle.prepare_calls, 1)

    def test_prepare_failure_triggers_recovery_attempt(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        lifecycle = _FakeLifecycle(prepare_error=RuntimeError("prepare"))
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(_outcome(artifact)),
            lifecycle=lifecycle,
        )
        with self.assertRaisesRegex(RuntimeError, "prepare"):
            runner.run(
                artifact,
                _manifest(artifact),
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )
        self.assertEqual(lifecycle.recover_calls, 1)

    def test_prepare_and_recovery_failure_surfaces_recovery_error(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        lifecycle = _FakeLifecycle(
            prepare_error=RuntimeError("prepare"),
            recover_error=RuntimeError("recover"),
        )
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(_outcome(artifact)),
            lifecycle=lifecycle,
        )
        with self.assertRaises(ControlledPropagationLiveRecoveryError):
            runner.run(
                artifact,
                _manifest(artifact),
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )

    def test_candidate_provider_relation_drift_is_rejected_before_injection(
        self,
    ) -> None:
        artifact = build_systemd_propagation_pair(
            _pair_spec(DependencyRelation.REQUIRES)
        )
        injector = _FakeInjector(_outcome(artifact))
        lifecycle = _FakeLifecycle()
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(
                DependencyRelation.WANTS
            ),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=injector,
            lifecycle=lifecycle,
        )
        with self.assertRaises(ControlledPropagationLivePreconditionError):
            runner.run(
                artifact,
                _manifest(artifact),
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )
        self.assertEqual(injector.calls, 0)

    def test_injector_failure_still_recovers_pair(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        lifecycle = _FakeLifecycle()
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(_outcome(artifact), error=RuntimeError("inject")),
            lifecycle=lifecycle,
        )
        with self.assertRaisesRegex(RuntimeError, "inject"):
            runner.run(
                artifact,
                _manifest(artifact),
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )
        self.assertEqual(lifecycle.recover_calls, 1)

    def test_injector_failure_plus_recovery_failure_reports_recovery_error(
        self,
    ) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        lifecycle = _FakeLifecycle(recover_error=RuntimeError("recover"))
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(_outcome(artifact), error=RuntimeError("inject")),
            lifecycle=lifecycle,
        )
        with self.assertRaises(ControlledPropagationLiveRecoveryError):
            runner.run(
                artifact,
                _manifest(artifact),
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )

    def test_runner_rejects_outcome_from_different_experiment(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        outcome = _outcome(artifact)
        manifest = replace(_manifest(artifact), experiment_id="exp-" + "2" * 32)
        lifecycle = _FakeLifecycle()
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(outcome),
            lifecycle=lifecycle,
        )
        with self.assertRaises(ControlledPropagationLiveContractError):
            runner.run(
                artifact,
                manifest,
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )
        self.assertEqual(lifecycle.recover_calls, 1)

    def test_successful_runner_builds_noncausal_live_record_and_recovers(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        outcome = _outcome(artifact)
        lifecycle = _FakeLifecycle()
        verifier_calls = []
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: verifier_calls.append(artifact.pair_id),
            boot_id_reader=lambda: _BOOT,
            candidate_provider=lambda artifact, boot: _candidate(
                artifact.spec.requirement_relation
            ),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(outcome),
            lifecycle=lifecycle,
        )
        live = runner.run(
            artifact,
            _manifest(artifact),
            ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
        )
        self.assertIsInstance(live, ControlledPropagationLiveRun)
        self.assertEqual(live.boot_id_before, _BOOT)
        self.assertEqual(live.boot_id_after, _BOOT)
        self.assertGreaterEqual(len(live.dependent_assessments), 1)
        self.assertEqual(lifecycle.prepare_calls, 1)
        self.assertEqual(lifecycle.recover_calls, 1)
        self.assertGreaterEqual(len(verifier_calls), 4)
        payload = live.to_dict()
        self.assertFalse(payload["causal_claim"])
        self.assertFalse(payload["universal_systemd_behavior_claim"])
        self.assertFalse(payload["probabilistic_confidence_assigned"])
        self.assertFalse(payload["unit_installation_performed_by_runner"])
        self.assertFalse(payload["privilege_escalation_performed_by_runner"])
        self.assertFalse(payload["shell_invocation_performed_by_runner"])

    def test_successful_runner_detects_boot_change_after_recovery(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        boots = iter((_BOOT, _BOOT, _BOOT, "b" * 32))
        runner = ControlledPropagationLiveRunner(
            pair_verifier=lambda artifact: None,
            boot_id_reader=lambda: next(boots),
            candidate_provider=lambda artifact, boot: _candidate(),
            sampler_factory=lambda unit: _SequenceSampler(unit),
            injector=_FakeInjector(_outcome(artifact)),
            lifecycle=_FakeLifecycle(),
        )
        with self.assertRaises(ControlledPropagationLiveContractError):
            runner.run(
                artifact,
                _manifest(artifact),
                ControlledPropagationLivePolicy(max_sample_gap_usec=250_000),
            )

    def test_live_run_model_rejects_false_post_recovery_verification(self) -> None:
        artifact = build_systemd_propagation_pair(_pair_spec())
        candidate = _candidate()
        outcome = _outcome(artifact)
        assessments = (
            _assessment_evidence(
                _DEPENDENT,
                digit="1",
                assessed_usec=1_200_000,
                state_change_usec=800_000,
                status=SystemdServiceHealthStatus.HEALTHY,
            ),
        )
        coverage = evaluate_controlled_ground_truth_coverage(
            candidate,
            outcome.ground_truth,
            _BOOT,
            assessments,
            analysis_window_usec=500_000,
            max_sample_gap_usec=500_000,
        )
        record = build_controlled_propagation_experiment_record(
            artifact, outcome, candidate, coverage
        )
        with self.assertRaises(ControlledPropagationLiveContractError):
            ControlledPropagationLiveRun(
                pair_artifact=artifact,
                policy=ControlledPropagationLivePolicy(max_sample_gap_usec=500_000),
                candidate=candidate,
                injection_outcome=outcome,
                dependent_assessments=assessments,
                experiment_record=record,
                boot_id_before=_BOOT,
                boot_id_after=_BOOT,
                post_recovery_verified=False,
            )


if __name__ == "__main__":
    unittest.main()
