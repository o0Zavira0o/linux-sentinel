from __future__ import annotations

import json
import threading
import time
import unittest
from collections.abc import Sequence
from datetime import datetime, timezone

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.lab import (
    FaultExperimentBenchmark,
    FaultExperimentManifest,
    FaultExperimentRun,
    FaultExperimentTelemetrySample,
    FaultGroundTruthWindow,
    FaultInjectionOutcome,
    FaultLabExperimentContractError,
    FaultLabTelemetryError,
    FaultMode,
    FaultScenario,
    LabFaultOperation,
    SystemdExperimentTelemetryCapture,
    SystemdFaultExperimentOrchestrator,
    SystemdLabFaultPlan,
    SystemdLabFixtureSpec,
    SystemdLabServiceState,
    build_fault_experiment_benchmark,
    build_systemd_lab_fixture,
)
from sentinel_x.systemd import SystemdCorrelationBasis
from sentinel_x.systemd.correlation_models import (
    SystemdCorrelationMatch,
    SystemdTemporalCorrelationReport,
    SystemdTemporalRelation,
)
from sentinel_x.systemd.observation import SystemdServiceEmission

_BOOT_ID = "a" * 32
_UNIT = "sentinel-x-lab-orchestrator-01.service"


def _scenario(mode: FaultMode = FaultMode.SERVICE_FAILED) -> FaultScenario:
    return FaultScenario(
        scenario_id="orchestrator-live",
        description="Bounded experiment orchestration test.",
        target_unit=_UNIT,
        fault_mode=mode,
        evidence_grace_seconds=1.0,
    )


def _manifest(mode: FaultMode = FaultMode.SERVICE_FAILED) -> FaultExperimentManifest:
    return FaultExperimentManifest(
        _scenario(mode),
        experiment_id="exp-0123456789abcdef0123456789abcdef",
        created_at=datetime(2026, 8, 14, tzinfo=timezone.utc),
    )


def _artifact():
    return build_systemd_lab_fixture(
        SystemdLabFixtureSpec("orchestrator-01", runtime_max_seconds=120)
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
    start = 2_000_000
    truth = FaultGroundTruthWindow.from_manifest(
        manifest,
        started_at=datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc),
        started_monotonic_usec=start,
    ).close(
        ended_at=datetime(2026, 8, 14, 10, 0, 1, tzinfo=timezone.utc),
        ended_monotonic_usec=3_000_000,
    )
    operation = (
        LabFaultOperation.STOP_SERVICE
        if mode is FaultMode.SERVICE_INACTIVE
        else LabFaultOperation.ABORT_MAIN_PROCESS
    )
    expected = ("inactive",) if mode is FaultMode.SERVICE_INACTIVE else ("failed",)
    return FaultInjectionOutcome(
        plan=SystemdLabFaultPlan(
            experiment_id=manifest.experiment_id,
            scenario_id=manifest.scenario.scenario_id,
            target_unit=_UNIT,
            fault_mode=mode,
            operation=operation,
            expected_fault_active_states=expected,
            recovery_operations=("start", "reset-failed"),
            artifact_sha256=_artifact().sha256,
        ),
        ground_truth=truth,
        baseline_state=_state("active", "running", 10, "success"),
        fault_state=_state(
            expected[0],
            "failed" if expected[0] == "failed" else "dead",
            0,
            "signal" if expected[0] == "failed" else "success",
        ),
        recovered_state=_state("active", "running", 20, "success"),
    )


def _sample(
    observed: int,
    active: str,
    *,
    pid: int | None,
    state_change: int | None = None,
    journal: Sequence[int] = (),
    correlated: Sequence[int] = (),
    exact: Sequence[int] = (),
) -> FaultExperimentTelemetrySample:
    return FaultExperimentTelemetrySample(
        observed_monotonic_usec=observed,
        service_event_id=f"service-{observed}",
        service_active_state=active,
        service_sub_state="running" if active == "active" else active,
        service_main_pid=pid,
        service_state_change_monotonic_usec=state_change,
        journal_event_id=f"journal-{observed}" if journal else None,
        journal_entry_monotonic_usec=tuple(journal),
        correlated_entry_monotonic_usec=tuple(correlated),
        exact_invocation_entry_monotonic_usec=tuple(exact),
    )


class _FakeInjector:
    def __init__(self, result: FaultInjectionOutcome | BaseException) -> None:
        self.result = result
        self.calls = 0

    def execute(self, manifest, artifact):
        self.calls += 1
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _FakeCapture:
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


class _ServiceCollector:
    def __init__(self, event: SentinelEvent) -> None:
        self.event = event

    def collect(self) -> SystemdServiceEmission:
        return SystemdServiceEmission("lab.experiment.service", self.event)


class _JournalCollector:
    def __init__(self, event: SentinelEvent | None) -> None:
        self._event = event
        self.commits = 0
        self.rollbacks = 0

    @property
    def name(self) -> str:
        return "lab.experiment.journal"

    def collect(self):
        return _JournalEmissionStub(self, self._event)


class _JournalEmissionStub:
    def __init__(
        self,
        collector: _JournalCollector,
        event: SentinelEvent | None,
    ) -> None:
        self.event = event
        self._collector = collector

    def commit_publication(self) -> None:
        self._collector.commits += 1

    def rollback_publication(self) -> None:
        self._collector.rollbacks += 1


class _Correlator:
    def __init__(self, report: SystemdTemporalCorrelationReport) -> None:
        self.report = report

    def correlate(self, service_event, journal_event):
        return self.report


def _service_event(
    active="failed",
    sub="failed",
    pid=None,
    state_change=2_000_000,
) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.systemd.service",
        message="service",
        attributes={
            "observation_type": "linux.systemd.service",
            "active_state": active,
            "sub_state": sub,
            "main_pid": pid,
            "state_change_monotonic_usec": state_change,
        },
        occurred_at=datetime.now(timezone.utc),
    )


def _journal_event(timestamps=(2_100_000, 2_200_000)) -> SentinelEvent:
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source="sentinel_x.systemd.journal",
        message="journal",
        attributes={
            "observation_type": "linux.systemd.journal",
            "entries": [{"monotonic_timestamp_usec": value} for value in timestamps],
        },
        occurred_at=datetime.now(timezone.utc),
    )


def _report(service_event, journal_event) -> SystemdTemporalCorrelationReport:
    return SystemdTemporalCorrelationReport(
        service_event_id=service_event.event_id,
        journal_event_id=journal_event.event_id,
        requested_unit=_UNIT,
        canonical_unit=_UNIT,
        boot_id=_BOOT_ID,
        evaluated_entry_count=2,
        matches=(
            SystemdCorrelationMatch(
                service_event_id=service_event.event_id,
                journal_event_id=journal_event.event_id,
                journal_entry_index=0,
                requested_unit=_UNIT,
                canonical_unit=_UNIT,
                boot_id=_BOOT_ID,
                basis=SystemdCorrelationBasis.EXACT_INVOCATION,
                temporal_relation=SystemdTemporalRelation.AT,
                monotonic_delta_usec=0,
                service_invocation_id="b" * 32,
                matched_journal_invocation_id="b" * 32,
                journal_cursor_sha256="c" * 64,
                journal_priority=6,
                journal_message="failed",
            ),
        ),
    )


class FaultLabOrchestratorTests(unittest.TestCase):
    def test_sample_exposes_health_and_serializes(self) -> None:
        sample = _sample(3_100_000, "active", pid=7)
        self.assertTrue(sample.service_is_healthy)
        json.dumps(sample.to_dict())

    def test_sample_requires_correlated_timestamps_from_same_journal(self) -> None:
        with self.assertRaises(FaultLabExperimentContractError):
            _sample(
                2_100_000,
                "failed",
                pid=None,
                journal=(2_100_000,),
                correlated=(2_200_000,),
            )

    def test_sample_requires_exact_timestamps_to_be_correlated(self) -> None:
        with self.assertRaises(FaultLabExperimentContractError):
            _sample(
                2_100_000,
                "failed",
                pid=None,
                journal=(2_100_000,),
                exact=(2_100_000,),
            )

    def test_sample_rejects_journal_timestamps_without_event_identity(self) -> None:
        with self.assertRaises(FaultLabExperimentContractError):
            FaultExperimentTelemetrySample(
                1,
                "s",
                "active",
                "running",
                1,
                None,
                None,
                (1,),
                (),
                (),
            )

    def test_sample_rejects_regressing_timestamp_tuple(self) -> None:
        with self.assertRaises(FaultLabExperimentContractError):
            _sample(2_100_000, "failed", pid=None, journal=(5, 4))

    def test_benchmark_derives_transition_aligned_latencies(self) -> None:
        samples = (
            _sample(1_900_000, "active", pid=10, state_change=1_000_000),
            _sample(
                2_100_000,
                "failed",
                pid=None,
                state_change=1_980_000,
                journal=(1_990_000, 2_020_000),
                correlated=(2_020_000,),
                exact=(2_020_000,),
            ),
            _sample(
                2_500_000,
                "failed",
                pid=None,
                state_change=1_980_000,
                journal=(2_400_000,),
                correlated=(2_400_000,),
            ),
            _sample(3_150_000, "active", pid=20, state_change=3_000_000),
        )
        benchmark = build_fault_experiment_benchmark(_outcome(), samples)
        self.assertEqual(
            benchmark.reported_fault_transition_monotonic_usec,
            1_980_000,
        )
        self.assertEqual(benchmark.ground_truth_confirmation_lag_usec, 20_000)
        self.assertEqual(benchmark.service_fault_visibility_latency_usec, 120_000)
        self.assertEqual(benchmark.journal_fault_evidence_latency_usec, 10_000)
        self.assertEqual(
            benchmark.correlated_fault_evidence_latency_usec,
            40_000,
        )
        self.assertEqual(
            benchmark.exact_invocation_evidence_latency_usec,
            40_000,
        )
        self.assertEqual(benchmark.recovery_visibility_latency_usec, 150_000)
        self.assertTrue(benchmark.core_coverage_complete)
        self.assertTrue(benchmark.correlation_coverage)
        self.assertTrue(benchmark.exact_invocation_coverage)

    def test_benchmark_keeps_missing_transition_visibility_explicit(self) -> None:
        benchmark = build_fault_experiment_benchmark(
            _outcome(),
            (
                _sample(2_100_000, "failed", pid=None),
                _sample(3_100_000, "active", pid=20),
            ),
        )
        self.assertIsNone(benchmark.reported_fault_transition_monotonic_usec)
        self.assertIsNone(benchmark.ground_truth_confirmation_lag_usec)
        self.assertIsNone(benchmark.service_fault_visibility_latency_usec)
        self.assertIsNone(benchmark.journal_fault_evidence_latency_usec)
        self.assertEqual(benchmark.recovery_visibility_latency_usec, 100_000)
        self.assertFalse(benchmark.core_coverage_complete)

    def test_benchmark_uses_only_evidence_seen_with_fault_state(self) -> None:
        benchmark = build_fault_experiment_benchmark(
            _outcome(),
            (
                _sample(
                    1_900_000,
                    "active",
                    pid=10,
                    state_change=1_000_000,
                    journal=(1_950_000,),
                ),
                _sample(
                    2_100_000,
                    "failed",
                    pid=None,
                    state_change=1_980_000,
                    journal=(1_970_000, 1_990_000),
                ),
                _sample(
                    3_100_000,
                    "active",
                    pid=20,
                    state_change=3_000_000,
                    journal=(3_050_000,),
                ),
            ),
        )
        self.assertEqual(benchmark.fault_journal_entry_count, 2)
        self.assertEqual(benchmark.journal_fault_evidence_latency_usec, 10_000)

    def test_benchmark_counts_pretransition_exact_evidence_without_latency(
        self,
    ) -> None:
        benchmark = build_fault_experiment_benchmark(
            _outcome(),
            (
                _sample(
                    2_100_000,
                    "failed",
                    pid=None,
                    state_change=2_000_000,
                    journal=(1_999_999,),
                    correlated=(1_999_999,),
                    exact=(1_999_999,),
                ),
                _sample(
                    3_100_000,
                    "active",
                    pid=20,
                    state_change=3_000_000,
                ),
            ),
        )
        self.assertEqual(benchmark.fault_journal_entry_count, 1)
        self.assertEqual(benchmark.fault_correlated_entry_count, 1)
        self.assertEqual(benchmark.fault_exact_invocation_entry_count, 1)
        self.assertIsNone(benchmark.journal_fault_evidence_latency_usec)
        self.assertIsNone(benchmark.correlated_fault_evidence_latency_usec)
        self.assertIsNone(benchmark.exact_invocation_evidence_latency_usec)
        self.assertTrue(benchmark.correlation_coverage)
        self.assertTrue(benchmark.exact_invocation_coverage)

    def test_benchmark_accepts_transition_and_recovery_boundaries(self) -> None:
        benchmark = build_fault_experiment_benchmark(
            _outcome(),
            (
                _sample(
                    2_000_000,
                    "failed",
                    pid=None,
                    state_change=2_000_000,
                    journal=(2_000_000,),
                    correlated=(2_000_000,),
                ),
                _sample(3_000_000, "active", pid=20, state_change=3_000_000),
            ),
        )
        self.assertEqual(benchmark.service_fault_visibility_latency_usec, 0)
        self.assertEqual(benchmark.journal_fault_evidence_latency_usec, 0)
        self.assertEqual(benchmark.recovery_visibility_latency_usec, 0)

    def test_benchmark_rejects_untyped_samples(self) -> None:
        with self.assertRaises(FaultLabExperimentContractError):
            build_fault_experiment_benchmark(_outcome(), [object()])

    def test_benchmark_serialization_is_json_friendly(self) -> None:
        benchmark = build_fault_experiment_benchmark(
            _outcome(),
            (
                _sample(
                    2_100_000,
                    "failed",
                    pid=None,
                    state_change=1_980_000,
                ),
                _sample(
                    3_100_000,
                    "active",
                    pid=20,
                    state_change=3_000_000,
                ),
            ),
        )
        json.dumps(benchmark.to_dict())

    def test_benchmark_rejects_impossible_counter_relationships(self) -> None:
        with self.assertRaises(FaultLabExperimentContractError):
            FaultExperimentBenchmark(
                "exp-x",
                "scenario",
                _UNIT,
                "service_failed",
                1,
                1,
                1,
                0,
                1,
                0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )

    def test_run_validates_benchmark_identity_and_sample_count(self) -> None:
        outcome = _outcome()
        benchmark = build_fault_experiment_benchmark(outcome, ())
        run = FaultExperimentRun(outcome, benchmark, ())
        json.dumps(run.to_dict())
        with self.assertRaises(FaultLabExperimentContractError):
            FaultExperimentRun(outcome, benchmark, (_sample(1, "active", pid=1),))

    def test_orchestrator_starts_capture_before_injection_and_finishes_after(
        self,
    ) -> None:
        order: list[str] = []
        outcome = _outcome()

        class Injector:
            def execute(self, manifest, artifact):
                order.append("inject")
                return outcome

        class Capture(_FakeCapture):
            def start(self):
                order.append("start")
                super().start()

            def finish(self, *, post_recovery_grace_seconds):
                order.append("finish")
                return super().finish(
                    post_recovery_grace_seconds=post_recovery_grace_seconds
                )

        capture = Capture(
            (
                _sample(
                    2_100_000,
                    "failed",
                    pid=None,
                    state_change=1_980_000,
                ),
                _sample(
                    3_100_000,
                    "active",
                    pid=20,
                    state_change=3_000_000,
                ),
            )
        )
        run = SystemdFaultExperimentOrchestrator(
            injector=Injector(),
            capture_factory=lambda unit: capture,
            post_recovery_grace_seconds=0.25,
        ).run(_manifest(), _artifact())
        self.assertEqual(order, ["start", "inject", "finish"])
        self.assertEqual(capture.grace_values, [0.25])
        self.assertEqual(run.outcome, outcome)

    def test_orchestrator_stops_capture_without_grace_when_injection_fails(
        self,
    ) -> None:
        capture = _FakeCapture(())
        orchestrator = SystemdFaultExperimentOrchestrator(
            injector=_FakeInjector(RuntimeError("boom")),
            capture_factory=lambda unit: capture,
        )
        with self.assertRaisesRegex(RuntimeError, "boom"):
            orchestrator.run(_manifest(), _artifact())
        self.assertEqual(capture.finished, 1)
        self.assertEqual(capture.grace_values, [0.0])

    def test_orchestrator_does_not_inject_when_capture_start_fails(self) -> None:
        injector = _FakeInjector(_outcome())
        capture = _FakeCapture((), start_error=FaultLabTelemetryError("no telemetry"))
        orchestrator = SystemdFaultExperimentOrchestrator(
            injector=injector, capture_factory=lambda unit: capture
        )
        with self.assertRaises(FaultLabTelemetryError):
            orchestrator.run(_manifest(), _artifact())
        self.assertEqual(injector.calls, 0)

    def test_orchestrator_rejects_manifest_artifact_mismatch(self) -> None:
        other = build_systemd_lab_fixture(SystemdLabFixtureSpec("other-01"))
        with self.assertRaises(FaultLabExperimentContractError):
            SystemdFaultExperimentOrchestrator().run(_manifest(), other)

    def test_orchestrator_rejects_invalid_capture_contract_before_mutation(
        self,
    ) -> None:
        injector = _FakeInjector(_outcome())
        orchestrator = SystemdFaultExperimentOrchestrator(
            injector=injector, capture_factory=lambda unit: object()
        )
        with self.assertRaises(FaultLabExperimentContractError):
            orchestrator.run(_manifest(), _artifact())
        self.assertEqual(injector.calls, 0)

    def test_orchestrator_constructor_bounds_grace(self) -> None:
        for value in (True, -1.0, 11.0):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    SystemdFaultExperimentOrchestrator(
                        post_recovery_grace_seconds=value
                    )

    def test_capture_sample_once_commits_valid_journal_event(self) -> None:
        service = _service_event()
        journal = _journal_event()
        journal_collector = _JournalCollector(journal)
        capture = SystemdExperimentTelemetryCapture(
            _UNIT,
            service_collector=_ServiceCollector(service),
            journal_collector=journal_collector,
            correlator=_Correlator(_report(service, journal)),
            monotonic_clock=lambda: 2.3,
        )
        sample = capture.sample_once()
        self.assertEqual(sample.observed_monotonic_usec, 2_300_000)
        self.assertEqual(sample.journal_entry_monotonic_usec, (2_100_000, 2_200_000))
        self.assertEqual(sample.correlated_entry_monotonic_usec, (2_100_000,))
        self.assertEqual(sample.exact_invocation_entry_monotonic_usec, (2_100_000,))
        self.assertEqual(journal_collector.commits, 1)
        self.assertEqual(journal_collector.rollbacks, 0)

    def test_capture_sample_once_rolls_back_on_projection_failure(self) -> None:
        service = _service_event()
        journal = _journal_event()
        journal_collector = _JournalCollector(journal)

        class BrokenCorrelator:
            def correlate(self, service_event, journal_event):
                raise RuntimeError("broken")

        capture = SystemdExperimentTelemetryCapture(
            _UNIT,
            service_collector=_ServiceCollector(service),
            journal_collector=journal_collector,
            correlator=BrokenCorrelator(),
        )
        with self.assertRaisesRegex(RuntimeError, "broken"):
            capture.sample_once()
        self.assertEqual(journal_collector.commits, 0)
        self.assertEqual(journal_collector.rollbacks, 1)

    def test_capture_sample_once_supports_eventless_journal_poll(self) -> None:
        capture = SystemdExperimentTelemetryCapture(
            _UNIT,
            service_collector=_ServiceCollector(_service_event("active", "running", 7)),
            journal_collector=_JournalCollector(None),
            monotonic_clock=lambda: 1.0,
        )
        sample = capture.sample_once()
        self.assertIsNone(sample.journal_event_id)
        self.assertEqual(sample.journal_entry_monotonic_usec, ())

    def test_capture_constructor_rejects_invalid_bounds(self) -> None:
        with self.assertRaises(ValueError):
            SystemdExperimentTelemetryCapture(_UNIT, poll_interval_seconds=0.001)
        with self.assertRaises(TypeError):
            SystemdExperimentTelemetryCapture(_UNIT, max_samples=True)
        with self.assertRaises(ValueError):
            SystemdExperimentTelemetryCapture(_UNIT, max_samples=1)

    def test_capture_start_requires_first_successful_sample(self) -> None:
        class FailingService:
            def collect(self):
                raise RuntimeError("read failed")

        capture = SystemdExperimentTelemetryCapture(
            _UNIT,
            service_collector=FailingService(),
            journal_collector=_JournalCollector(None),
            startup_timeout_seconds=0.2,
            poll_interval_seconds=0.02,
        )
        with self.assertRaises(FaultLabTelemetryError):
            capture.start()

    def test_capture_start_and_finish_return_bounded_samples(self) -> None:
        capture = SystemdExperimentTelemetryCapture(
            _UNIT,
            service_collector=_ServiceCollector(_service_event("active", "running", 7)),
            journal_collector=_JournalCollector(None),
            poll_interval_seconds=0.02,
            startup_timeout_seconds=0.5,
        )
        capture.start()
        time.sleep(0.05)
        samples = capture.finish(post_recovery_grace_seconds=0.0)
        self.assertGreaterEqual(len(samples), 1)
        self.assertTrue(all(sample.service_is_healthy for sample in samples))

    def test_capture_cannot_start_twice_or_finish_twice(self) -> None:
        capture = SystemdExperimentTelemetryCapture(
            _UNIT,
            service_collector=_ServiceCollector(_service_event("active", "running", 7)),
            journal_collector=_JournalCollector(None),
            poll_interval_seconds=0.02,
            startup_timeout_seconds=0.5,
        )
        capture.start()
        with self.assertRaises(FaultLabTelemetryError):
            capture.start()
        capture.finish(post_recovery_grace_seconds=0.0)
        with self.assertRaises(FaultLabTelemetryError):
            capture.finish(post_recovery_grace_seconds=0.0)

    def test_capture_failure_after_start_is_reported_at_finish(self) -> None:
        counter = 0

        class Service:
            def collect(self):
                nonlocal counter
                counter += 1
                if counter >= 2:
                    raise RuntimeError("late failure")
                return SystemdServiceEmission(
                    "lab.experiment.service",
                    _service_event("active", "running", 7),
                )

        capture = SystemdExperimentTelemetryCapture(
            _UNIT,
            service_collector=Service(),
            journal_collector=_JournalCollector(None),
            poll_interval_seconds=0.02,
            startup_timeout_seconds=0.5,
        )
        capture.start()
        time.sleep(0.05)
        with self.assertRaises(FaultLabTelemetryError):
            capture.finish(post_recovery_grace_seconds=0.0)

    def test_capture_thread_names_are_bounded_and_daemonized(self) -> None:
        capture = SystemdExperimentTelemetryCapture(
            _UNIT,
            service_collector=_ServiceCollector(_service_event("active", "running", 7)),
            journal_collector=_JournalCollector(None),
            poll_interval_seconds=0.02,
            startup_timeout_seconds=0.5,
        )
        capture.start()
        threads = [
            thread
            for thread in threading.enumerate()
            if "sentinel-x-lab-capture" in thread.name
        ]
        self.assertTrue(any(thread.daemon for thread in threads))
        capture.finish(post_recovery_grace_seconds=0.0)


if __name__ == "__main__":
    unittest.main()
