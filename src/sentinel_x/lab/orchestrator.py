"""Bounded experiment orchestration and telemetry benchmarking for Sentinel-X."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Final, Protocol

from sentinel_x.core.events import SentinelEvent
from sentinel_x.lab.fixture import (
    SystemdLabFixtureArtifact,
    validate_lab_fixture_unit_name,
)
from sentinel_x.lab.injector import FaultInjectionOutcome, SystemdLabFaultInjector
from sentinel_x.lab.models import FaultExperimentManifest
from sentinel_x.systemd import (
    SYSTEMD_JOURNAL_OBSERVATION_TYPE,
    StatefulSystemdJournalCollector,
    SystemdCorrelationBasis,
    SystemdServiceCollector,
    SystemdTemporalCorrelator,
)

_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 0.20
_MIN_POLL_INTERVAL_SECONDS: Final[float] = 0.02
_MAX_POLL_INTERVAL_SECONDS: Final[float] = 5.0
_DEFAULT_STARTUP_TIMEOUT_SECONDS: Final[float] = 5.0
_MAX_STARTUP_TIMEOUT_SECONDS: Final[float] = 30.0
_DEFAULT_POST_RECOVERY_GRACE_SECONDS: Final[float] = 0.50
_MAX_POST_RECOVERY_GRACE_SECONDS: Final[float] = 10.0
_DEFAULT_JOIN_TIMEOUT_SECONDS: Final[float] = 5.0
_MAX_JOIN_TIMEOUT_SECONDS: Final[float] = 30.0
_DEFAULT_MAX_SAMPLES: Final[int] = 4096
_MIN_MAX_SAMPLES: Final[int] = 8
_MAX_MAX_SAMPLES: Final[int] = 16384
_SERVICE_COLLECTOR_NAME: Final[str] = "lab.experiment.service"
_JOURNAL_COLLECTOR_NAME: Final[str] = "lab.experiment.journal"
_JOURNAL_MAX_ENTRIES: Final[int] = 32


class FaultLabExperimentError(RuntimeError):
    """Base error for bounded fault-laboratory experiment orchestration."""


class FaultLabTelemetryError(FaultLabExperimentError):
    """Raised when read-only experiment telemetry cannot be captured safely."""


class FaultLabExperimentContractError(FaultLabExperimentError):
    """Raised when experiment inputs or derived benchmark data are inconsistent."""


class MonotonicClock(Protocol):
    """Structural monotonic clock dependency."""

    def __call__(self) -> float:
        """Return monotonic seconds."""

        ...


class FaultInjector(Protocol):
    """Structural fault injector dependency consumed by the orchestrator."""

    def execute(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultInjectionOutcome:
        """Execute one bounded recovery-first fault experiment."""

        ...


class ExperimentTelemetryCapture(Protocol):
    """Structural capture dependency consumed by the orchestrator."""

    def start(self) -> None:
        """Establish read-only telemetry before fault mutation begins."""

        ...

    def finish(
        self,
        *,
        post_recovery_grace_seconds: float,
    ) -> tuple[FaultExperimentTelemetrySample, ...]:
        """Stop capture after a bounded recovery-visibility grace interval."""

        ...


class TelemetryCaptureFactory(Protocol):
    """Factory for one experiment-scoped telemetry capture."""

    def __call__(self, unit_name: str) -> ExperimentTelemetryCapture:
        """Build a capture bound to one canonical lab unit."""

        ...


@dataclass(frozen=True, slots=True)
class FaultExperimentTelemetrySample:
    """One compact service/journal telemetry sample in boot-monotonic time."""

    observed_monotonic_usec: int
    service_event_id: str
    service_active_state: str
    service_sub_state: str
    service_main_pid: int | None
    service_state_change_monotonic_usec: int | None
    journal_event_id: str | None
    journal_entry_monotonic_usec: tuple[int, ...]
    correlated_entry_monotonic_usec: tuple[int, ...]
    exact_invocation_entry_monotonic_usec: tuple[int, ...]

    def __post_init__(self) -> None:
        """Validate compact telemetry invariants."""

        _validate_nonnegative_int(
            self.observed_monotonic_usec,
            field_name="observed_monotonic_usec",
        )
        _validate_nonempty_text(self.service_event_id, field_name="service_event_id")
        _validate_nonempty_text(
            self.service_active_state,
            field_name="service_active_state",
        )
        _validate_nonempty_text(
            self.service_sub_state,
            field_name="service_sub_state",
        )
        if self.service_main_pid is not None:
            _validate_nonnegative_int(
                self.service_main_pid,
                field_name="service_main_pid",
            )
        if self.service_state_change_monotonic_usec is not None:
            _validate_nonnegative_int(
                self.service_state_change_monotonic_usec,
                field_name="service_state_change_monotonic_usec",
            )
        if self.journal_event_id is not None:
            _validate_nonempty_text(
                self.journal_event_id,
                field_name="journal_event_id",
            )
        for field_name, values in (
            ("journal_entry_monotonic_usec", self.journal_entry_monotonic_usec),
            ("correlated_entry_monotonic_usec", self.correlated_entry_monotonic_usec),
            (
                "exact_invocation_entry_monotonic_usec",
                self.exact_invocation_entry_monotonic_usec,
            ),
        ):
            _validate_usec_tuple(values, field_name=field_name)
        journal_values = set(self.journal_entry_monotonic_usec)
        correlated_values = set(self.correlated_entry_monotonic_usec)
        exact_values = set(self.exact_invocation_entry_monotonic_usec)
        if not correlated_values.issubset(journal_values):
            raise FaultLabExperimentContractError(
                "correlated journal timestamps must come from the same journal sample"
            )
        if not exact_values.issubset(correlated_values):
            raise FaultLabExperimentContractError(
                "exact invocation timestamps must also be correlated timestamps"
            )
        if self.journal_event_id is None and journal_values:
            raise FaultLabExperimentContractError(
                "journal timestamps require a journal_event_id"
            )

    @property
    def service_is_healthy(self) -> bool:
        """Return whether the sampled service is visibly active and running."""

        return (
            self.service_active_state == "active"
            and self.service_sub_state == "running"
            and self.service_main_pid is not None
            and self.service_main_pid > 0
        )

    def to_dict(self) -> dict[str, object]:
        """Return a stable JSON-friendly telemetry representation."""

        return {
            "observed_monotonic_usec": self.observed_monotonic_usec,
            "service_event_id": self.service_event_id,
            "service_active_state": self.service_active_state,
            "service_sub_state": self.service_sub_state,
            "service_main_pid": self.service_main_pid,
            "service_state_change_monotonic_usec": (
                self.service_state_change_monotonic_usec
            ),
            "service_is_healthy": self.service_is_healthy,
            "journal_event_id": self.journal_event_id,
            "journal_entry_monotonic_usec": list(self.journal_entry_monotonic_usec),
            "correlated_entry_monotonic_usec": list(
                self.correlated_entry_monotonic_usec
            ),
            "exact_invocation_entry_monotonic_usec": list(
                self.exact_invocation_entry_monotonic_usec
            ),
        }


@dataclass(frozen=True, slots=True)
class FaultExperimentBenchmark:
    """Ground-truth-aligned visibility metrics for one completed experiment."""

    experiment_id: str
    scenario_id: str
    target_unit: str
    fault_mode: str
    ground_truth_duration_usec: int
    telemetry_sample_count: int
    fault_service_sample_count: int
    fault_journal_entry_count: int
    fault_correlated_entry_count: int
    fault_exact_invocation_entry_count: int
    reported_fault_transition_monotonic_usec: int | None
    ground_truth_confirmation_lag_usec: int | None
    service_fault_visibility_latency_usec: int | None
    journal_fault_evidence_latency_usec: int | None
    correlated_fault_evidence_latency_usec: int | None
    exact_invocation_evidence_latency_usec: int | None
    recovery_visibility_latency_usec: int | None

    def __post_init__(self) -> None:
        """Validate benchmark counters and latency relationships."""

        for text_field_name, text_value in (
            ("experiment_id", self.experiment_id),
            ("scenario_id", self.scenario_id),
            ("target_unit", self.target_unit),
            ("fault_mode", self.fault_mode),
        ):
            _validate_nonempty_text(text_value, field_name=text_field_name)
        validate_lab_fixture_unit_name(self.target_unit)
        for count_field_name, count_value in (
            ("ground_truth_duration_usec", self.ground_truth_duration_usec),
            ("telemetry_sample_count", self.telemetry_sample_count),
            ("fault_service_sample_count", self.fault_service_sample_count),
            ("fault_journal_entry_count", self.fault_journal_entry_count),
            ("fault_correlated_entry_count", self.fault_correlated_entry_count),
            (
                "fault_exact_invocation_entry_count",
                self.fault_exact_invocation_entry_count,
            ),
        ):
            _validate_nonnegative_int(count_value, field_name=count_field_name)
        if self.ground_truth_duration_usec <= 0:
            raise FaultLabExperimentContractError(
                "ground_truth_duration_usec must be positive"
            )
        if self.fault_service_sample_count > self.telemetry_sample_count:
            raise FaultLabExperimentContractError(
                "fault service sample count cannot exceed telemetry sample count"
            )
        if self.fault_correlated_entry_count > self.fault_journal_entry_count:
            raise FaultLabExperimentContractError(
                "correlated entry count cannot exceed journal entry count"
            )
        if self.fault_exact_invocation_entry_count > self.fault_correlated_entry_count:
            raise FaultLabExperimentContractError(
                "exact invocation entry count cannot exceed correlated entry count"
            )
        if self.reported_fault_transition_monotonic_usec is not None:
            _validate_nonnegative_int(
                self.reported_fault_transition_monotonic_usec,
                field_name="reported_fault_transition_monotonic_usec",
            )
        if self.ground_truth_confirmation_lag_usec is not None:
            _validate_nonnegative_int(
                self.ground_truth_confirmation_lag_usec,
                field_name="ground_truth_confirmation_lag_usec",
            )
        if (
            self.reported_fault_transition_monotonic_usec is None
            and self.ground_truth_confirmation_lag_usec is not None
        ):
            raise FaultLabExperimentContractError(
                "confirmation lag requires a reported fault transition"
            )
        for latency_field_name, latency_value in (
            (
                "service_fault_visibility_latency_usec",
                self.service_fault_visibility_latency_usec,
            ),
            (
                "journal_fault_evidence_latency_usec",
                self.journal_fault_evidence_latency_usec,
            ),
            (
                "correlated_fault_evidence_latency_usec",
                self.correlated_fault_evidence_latency_usec,
            ),
            (
                "exact_invocation_evidence_latency_usec",
                self.exact_invocation_evidence_latency_usec,
            ),
            ("recovery_visibility_latency_usec", self.recovery_visibility_latency_usec),
        ):
            if latency_value is not None:
                _validate_nonnegative_int(latency_value, field_name=latency_field_name)

    @property
    def core_coverage_complete(self) -> bool:
        """Return whether state, journal, and recovery visibility were all observed."""

        return (
            self.service_fault_visibility_latency_usec is not None
            and self.journal_fault_evidence_latency_usec is not None
            and self.recovery_visibility_latency_usec is not None
        )

    @property
    def correlation_coverage(self) -> bool:
        """Return whether at least one journal entry was correlated during the fault."""

        return self.fault_correlated_entry_count > 0

    @property
    def exact_invocation_coverage(self) -> bool:
        """Return whether exact invocation evidence existed during the fault."""

        return self.fault_exact_invocation_entry_count > 0

    def to_dict(self) -> dict[str, object]:
        """Return stable benchmark data for later dataset construction."""

        return {
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "target_unit": self.target_unit,
            "fault_mode": self.fault_mode,
            "ground_truth_duration_usec": self.ground_truth_duration_usec,
            "telemetry_sample_count": self.telemetry_sample_count,
            "fault_service_sample_count": self.fault_service_sample_count,
            "fault_journal_entry_count": self.fault_journal_entry_count,
            "fault_correlated_entry_count": self.fault_correlated_entry_count,
            "fault_exact_invocation_entry_count": (
                self.fault_exact_invocation_entry_count
            ),
            "reported_fault_transition_monotonic_usec": (
                self.reported_fault_transition_monotonic_usec
            ),
            "ground_truth_confirmation_lag_usec": (
                self.ground_truth_confirmation_lag_usec
            ),
            "service_fault_visibility_latency_usec": (
                self.service_fault_visibility_latency_usec
            ),
            "journal_fault_evidence_latency_usec": (
                self.journal_fault_evidence_latency_usec
            ),
            "correlated_fault_evidence_latency_usec": (
                self.correlated_fault_evidence_latency_usec
            ),
            "exact_invocation_evidence_latency_usec": (
                self.exact_invocation_evidence_latency_usec
            ),
            "recovery_visibility_latency_usec": self.recovery_visibility_latency_usec,
            "core_coverage_complete": self.core_coverage_complete,
            "correlation_coverage": self.correlation_coverage,
            "exact_invocation_coverage": self.exact_invocation_coverage,
        }


@dataclass(frozen=True, slots=True)
class FaultExperimentRun:
    """One completed injection outcome plus bounded telemetry benchmark evidence."""

    outcome: FaultInjectionOutcome
    benchmark: FaultExperimentBenchmark
    telemetry_samples: tuple[FaultExperimentTelemetrySample, ...]

    def __post_init__(self) -> None:
        """Ensure outcome, benchmark, and sample identities agree."""

        if not isinstance(self.outcome, FaultInjectionOutcome):
            raise FaultLabExperimentContractError(
                "outcome must be a FaultInjectionOutcome"
            )
        if not isinstance(self.benchmark, FaultExperimentBenchmark):
            raise FaultLabExperimentContractError(
                "benchmark must be a FaultExperimentBenchmark"
            )
        if not isinstance(self.telemetry_samples, tuple):
            raise FaultLabExperimentContractError("telemetry_samples must be a tuple")
        for sample in self.telemetry_samples:
            if not isinstance(sample, FaultExperimentTelemetrySample):
                raise FaultLabExperimentContractError(
                    "telemetry_samples must contain typed telemetry samples"
                )
        if self.benchmark.experiment_id != self.outcome.plan.experiment_id:
            raise FaultLabExperimentContractError(
                "benchmark experiment_id must match the injection outcome"
            )
        if self.benchmark.telemetry_sample_count != len(self.telemetry_samples):
            raise FaultLabExperimentContractError(
                "benchmark telemetry_sample_count must match captured samples"
            )

    def to_dict(self) -> dict[str, object]:
        """Return a bounded JSON-friendly experiment record."""

        return {
            "outcome": self.outcome.to_dict(),
            "benchmark": self.benchmark.to_dict(),
            "telemetry": [sample.to_dict() for sample in self.telemetry_samples],
        }


class SystemdExperimentTelemetryCapture:
    """Continuously sample frozen systemd/journal primitives around one experiment."""

    def __init__(
        self,
        unit_name: str,
        *,
        service_collector: SystemdServiceCollector | None = None,
        journal_collector: StatefulSystemdJournalCollector | None = None,
        correlator: SystemdTemporalCorrelator | None = None,
        monotonic_clock: MonotonicClock = time.monotonic,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        startup_timeout_seconds: float = _DEFAULT_STARTUP_TIMEOUT_SECONDS,
        join_timeout_seconds: float = _DEFAULT_JOIN_TIMEOUT_SECONDS,
        max_samples: int = _DEFAULT_MAX_SAMPLES,
    ) -> None:
        self._unit_name = validate_lab_fixture_unit_name(unit_name)
        self._service_collector = (
            SystemdServiceCollector(_SERVICE_COLLECTOR_NAME, self._unit_name)
            if service_collector is None
            else service_collector
        )
        self._journal_collector = (
            StatefulSystemdJournalCollector(
                _JOURNAL_COLLECTOR_NAME,
                self._unit_name,
                max_entries=_JOURNAL_MAX_ENTRIES,
            )
            if journal_collector is None
            else journal_collector
        )
        self._correlator = (
            SystemdTemporalCorrelator() if correlator is None else correlator
        )
        if not callable(monotonic_clock):
            raise TypeError("monotonic_clock must be callable")
        self._monotonic_clock = monotonic_clock
        self._poll_interval_seconds = _validate_seconds(
            poll_interval_seconds,
            field_name="poll_interval_seconds",
            minimum=_MIN_POLL_INTERVAL_SECONDS,
            maximum=_MAX_POLL_INTERVAL_SECONDS,
        )
        self._startup_timeout_seconds = _validate_seconds(
            startup_timeout_seconds,
            field_name="startup_timeout_seconds",
            minimum=_MIN_POLL_INTERVAL_SECONDS,
            maximum=_MAX_STARTUP_TIMEOUT_SECONDS,
        )
        self._join_timeout_seconds = _validate_seconds(
            join_timeout_seconds,
            field_name="join_timeout_seconds",
            minimum=_MIN_POLL_INTERVAL_SECONDS,
            maximum=_MAX_JOIN_TIMEOUT_SECONDS,
        )
        self._max_samples = _validate_sample_bound(max_samples)
        self._lock = Lock()
        self._stop_event = Event()
        self._ready_event = Event()
        self._failure_event = Event()
        self._samples: list[FaultExperimentTelemetrySample] = []
        self._failure: BaseException | None = None
        self._thread: Thread | None = None
        self._started = False
        self._finished = False

    @property
    def unit_name(self) -> str:
        """Return the lab unit bound to this capture."""

        return self._unit_name

    def start(self) -> None:
        """Start sampling and require one successful sample before mutation."""

        with self._lock:
            if self._started:
                raise FaultLabTelemetryError(
                    "telemetry capture can only be started once"
                )
            self._started = True
            self._thread = Thread(
                target=self._run_loop,
                name=f"sentinel-x-lab-capture-{self._unit_name}",
                daemon=True,
            )
            self._thread.start()
        if not self._ready_event.wait(self._startup_timeout_seconds):
            self._stop_and_join()
            raise FaultLabTelemetryError(
                "telemetry capture did not establish a baseline before timeout"
            )
        self._raise_if_failed()

    def finish(
        self,
        *,
        post_recovery_grace_seconds: float,
    ) -> tuple[FaultExperimentTelemetrySample, ...]:
        """Capture bounded post-recovery visibility, then stop and return samples."""

        grace = _validate_seconds(
            post_recovery_grace_seconds,
            field_name="post_recovery_grace_seconds",
            minimum=0.0,
            maximum=_MAX_POST_RECOVERY_GRACE_SECONDS,
            allow_zero=True,
        )
        with self._lock:
            if not self._started:
                raise FaultLabTelemetryError("telemetry capture has not been started")
            if self._finished:
                raise FaultLabTelemetryError("telemetry capture has already finished")
            self._finished = True
        if grace > 0.0:
            self._failure_event.wait(grace)
        self._stop_and_join()
        self._raise_if_failed()
        with self._lock:
            return tuple(self._samples)

    def sample_once(self) -> FaultExperimentTelemetrySample:
        """Collect one compact sample using frozen read-only telemetry primitives."""

        service_event = self._service_collector.collect().event
        journal_emission = self._journal_collector.collect()
        journal_event = journal_emission.event
        try:
            sample = _build_sample(
                service_event,
                journal_event,
                correlator=self._correlator,
                observed_monotonic_usec=_monotonic_usec(self._monotonic_clock),
            )
        except BaseException:
            if journal_event is not None:
                journal_emission.rollback_publication()
            raise
        if journal_event is not None:
            journal_emission.commit_publication()
        return sample

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                sample = self.sample_once()
                with self._lock:
                    if len(self._samples) >= self._max_samples:
                        raise FaultLabTelemetryError(
                            "telemetry sample bound reached before experiment finished"
                        )
                    self._samples.append(sample)
                self._ready_event.set()
                if self._stop_event.wait(self._poll_interval_seconds):
                    break
        except Exception as exc:
            with self._lock:
                self._failure = exc
            self._failure_event.set()
            self._ready_event.set()
            self._stop_event.set()

    def _stop_and_join(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(self._join_timeout_seconds)
        if thread.is_alive():
            raise FaultLabTelemetryError(
                "telemetry worker did not stop within the bounded join timeout"
            )

    def _raise_if_failed(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is None:
            return
        if isinstance(failure, FaultLabTelemetryError):
            raise failure
        raise FaultLabTelemetryError("experiment telemetry capture failed") from failure


class SystemdFaultExperimentOrchestrator:
    """Coordinate telemetry, bounded fault injection, recovery, and benchmarking."""

    def __init__(
        self,
        *,
        injector: FaultInjector | None = None,
        capture_factory: TelemetryCaptureFactory | None = None,
        post_recovery_grace_seconds: float = _DEFAULT_POST_RECOVERY_GRACE_SECONDS,
    ) -> None:
        self._injector: FaultInjector = (
            SystemdLabFaultInjector() if injector is None else injector
        )
        if not callable(getattr(self._injector, "execute", None)):
            raise TypeError("injector.execute must be callable")
        self._capture_factory: TelemetryCaptureFactory = (
            _default_capture_factory if capture_factory is None else capture_factory
        )
        if not callable(self._capture_factory):
            raise TypeError("capture_factory must be callable")
        self._post_recovery_grace_seconds = _validate_seconds(
            post_recovery_grace_seconds,
            field_name="post_recovery_grace_seconds",
            minimum=0.0,
            maximum=_MAX_POST_RECOVERY_GRACE_SECONDS,
            allow_zero=True,
        )

    def run(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultExperimentRun:
        """Run one recovery-first experiment with concurrent read-only telemetry."""

        _validate_run_inputs(manifest, artifact)
        capture = self._capture_factory(manifest.scenario.target_unit)
        _validate_capture(capture)
        capture.start()
        try:
            outcome = self._injector.execute(manifest, artifact)
        except BaseException:
            try:
                capture.finish(post_recovery_grace_seconds=0.0)
            except Exception:
                pass
            raise
        samples = capture.finish(
            post_recovery_grace_seconds=self._post_recovery_grace_seconds
        )
        benchmark = build_fault_experiment_benchmark(outcome, samples)
        return FaultExperimentRun(
            outcome=outcome,
            benchmark=benchmark,
            telemetry_samples=samples,
        )


def build_fault_experiment_benchmark(
    outcome: FaultInjectionOutcome,
    samples: Sequence[FaultExperimentTelemetrySample],
) -> FaultExperimentBenchmark:
    """Derive visibility latencies from closed truth and frozen telemetry."""

    if not isinstance(outcome, FaultInjectionOutcome):
        raise FaultLabExperimentContractError("outcome must be a FaultInjectionOutcome")
    normalized_samples = tuple(samples)
    for sample in normalized_samples:
        if not isinstance(sample, FaultExperimentTelemetrySample):
            raise FaultLabExperimentContractError(
                "samples must contain typed telemetry samples"
            )
    truth = outcome.ground_truth
    if truth.is_open or truth.ended_monotonic_usec is None:
        raise FaultLabExperimentContractError(
            "benchmarking requires closed ground truth"
        )
    duration_usec = truth.duration_usec
    if duration_usec is None or duration_usec <= 0:
        raise FaultLabExperimentContractError(
            "closed ground truth must have a positive duration"
        )

    start_usec = truth.started_monotonic_usec
    end_usec = truth.ended_monotonic_usec
    expected_states = set(outcome.plan.expected_fault_active_states)
    fault_service_samples = tuple(
        sample
        for sample in normalized_samples
        if sample.observed_monotonic_usec <= end_usec
        and sample.service_active_state in expected_states
    )
    transition_candidates: list[int] = []
    for sample in fault_service_samples:
        transition = sample.service_state_change_monotonic_usec
        if transition is not None and transition <= start_usec:
            transition_candidates.append(transition)
    transition_usec = max(transition_candidates) if transition_candidates else None

    fault_journal_timestamps = _timestamps_for_fault_samples(
        fault_service_samples,
        selector=lambda sample: sample.journal_entry_monotonic_usec,
        end_usec=end_usec,
    )
    fault_correlated_timestamps = _timestamps_for_fault_samples(
        fault_service_samples,
        selector=lambda sample: sample.correlated_entry_monotonic_usec,
        end_usec=end_usec,
    )
    fault_exact_timestamps = _timestamps_for_fault_samples(
        fault_service_samples,
        selector=lambda sample: sample.exact_invocation_entry_monotonic_usec,
        end_usec=end_usec,
    )
    latency_journal_timestamps = _timestamps_at_or_after_transition(
        fault_journal_timestamps,
        transition_usec=transition_usec,
    )
    latency_correlated_timestamps = _timestamps_at_or_after_transition(
        fault_correlated_timestamps,
        transition_usec=transition_usec,
    )
    latency_exact_timestamps = _timestamps_at_or_after_transition(
        fault_exact_timestamps,
        transition_usec=transition_usec,
    )
    recovery_samples = tuple(
        sample
        for sample in normalized_samples
        if sample.observed_monotonic_usec >= end_usec and sample.service_is_healthy
    )

    confirmation_lag = None if transition_usec is None else start_usec - transition_usec
    service_latency = (
        None
        if transition_usec is None
        else _first_sample_latency(
            fault_service_samples,
            reference_usec=transition_usec,
        )
    )
    recovery_latency = _first_sample_latency(
        recovery_samples,
        reference_usec=end_usec,
    )

    return FaultExperimentBenchmark(
        experiment_id=outcome.plan.experiment_id,
        scenario_id=outcome.plan.scenario_id,
        target_unit=outcome.plan.target_unit,
        fault_mode=outcome.plan.fault_mode.value,
        ground_truth_duration_usec=duration_usec,
        telemetry_sample_count=len(normalized_samples),
        fault_service_sample_count=len(fault_service_samples),
        fault_journal_entry_count=len(fault_journal_timestamps),
        fault_correlated_entry_count=len(fault_correlated_timestamps),
        fault_exact_invocation_entry_count=len(fault_exact_timestamps),
        reported_fault_transition_monotonic_usec=transition_usec,
        ground_truth_confirmation_lag_usec=confirmation_lag,
        service_fault_visibility_latency_usec=service_latency,
        journal_fault_evidence_latency_usec=_first_timestamp_latency(
            latency_journal_timestamps,
            reference_usec=transition_usec,
        ),
        correlated_fault_evidence_latency_usec=_first_timestamp_latency(
            latency_correlated_timestamps,
            reference_usec=transition_usec,
        ),
        exact_invocation_evidence_latency_usec=_first_timestamp_latency(
            latency_exact_timestamps,
            reference_usec=transition_usec,
        ),
        recovery_visibility_latency_usec=recovery_latency,
    )


def _default_capture_factory(unit_name: str) -> ExperimentTelemetryCapture:
    return SystemdExperimentTelemetryCapture(unit_name)


def _validate_run_inputs(
    manifest: FaultExperimentManifest,
    artifact: SystemdLabFixtureArtifact,
) -> None:
    if not isinstance(manifest, FaultExperimentManifest):
        raise FaultLabExperimentContractError(
            "manifest must be a FaultExperimentManifest"
        )
    if not isinstance(artifact, SystemdLabFixtureArtifact):
        raise FaultLabExperimentContractError(
            "artifact must be a SystemdLabFixtureArtifact"
        )
    if manifest.scenario.target_unit != artifact.unit_name:
        raise FaultLabExperimentContractError(
            "manifest target must match the canonical fixture artifact"
        )
    validate_lab_fixture_unit_name(artifact.unit_name)


def _validate_capture(capture: object) -> None:
    if not callable(getattr(capture, "start", None)):
        raise FaultLabExperimentContractError(
            "telemetry capture.start must be callable"
        )
    if not callable(getattr(capture, "finish", None)):
        raise FaultLabExperimentContractError(
            "telemetry capture.finish must be callable"
        )


def _build_sample(
    service_event: SentinelEvent,
    journal_event: SentinelEvent | None,
    *,
    correlator: SystemdTemporalCorrelator,
    observed_monotonic_usec: int,
) -> FaultExperimentTelemetrySample:
    if not isinstance(service_event, SentinelEvent):
        raise FaultLabTelemetryError("service collector returned an invalid event")
    service_active_state = _required_text_attribute(
        service_event.attributes,
        "active_state",
    )
    service_sub_state = _required_text_attribute(
        service_event.attributes,
        "sub_state",
    )
    service_main_pid = _optional_nonnegative_int_attribute(
        service_event.attributes,
        "main_pid",
    )
    state_change_monotonic_usec = _optional_nonnegative_int_attribute(
        service_event.attributes,
        "state_change_monotonic_usec",
    )
    if journal_event is None:
        return FaultExperimentTelemetrySample(
            observed_monotonic_usec=observed_monotonic_usec,
            service_event_id=service_event.event_id,
            service_active_state=service_active_state,
            service_sub_state=service_sub_state,
            service_main_pid=service_main_pid,
            service_state_change_monotonic_usec=state_change_monotonic_usec,
            journal_event_id=None,
            journal_entry_monotonic_usec=(),
            correlated_entry_monotonic_usec=(),
            exact_invocation_entry_monotonic_usec=(),
        )
    if not isinstance(journal_event, SentinelEvent):
        raise FaultLabTelemetryError("journal collector returned an invalid event")
    if (
        journal_event.attributes.get("observation_type")
        != SYSTEMD_JOURNAL_OBSERVATION_TYPE
    ):
        return FaultExperimentTelemetrySample(
            observed_monotonic_usec=observed_monotonic_usec,
            service_event_id=service_event.event_id,
            service_active_state=service_active_state,
            service_sub_state=service_sub_state,
            service_main_pid=service_main_pid,
            service_state_change_monotonic_usec=state_change_monotonic_usec,
            journal_event_id=journal_event.event_id,
            journal_entry_monotonic_usec=(),
            correlated_entry_monotonic_usec=(),
            exact_invocation_entry_monotonic_usec=(),
        )
    entry_timestamps = _journal_entry_timestamps(journal_event)
    report = correlator.correlate(service_event, journal_event)
    correlated: list[int] = []
    exact: list[int] = []
    for match in report.matches:
        try:
            timestamp = entry_timestamps[match.journal_entry_index]
        except IndexError as exc:
            raise FaultLabTelemetryError(
                "correlation referenced a journal entry outside the projected batch"
            ) from exc
        correlated.append(timestamp)
        if match.basis is SystemdCorrelationBasis.EXACT_INVOCATION:
            exact.append(timestamp)
    return FaultExperimentTelemetrySample(
        observed_monotonic_usec=observed_monotonic_usec,
        service_event_id=service_event.event_id,
        service_active_state=service_active_state,
        service_sub_state=service_sub_state,
        service_main_pid=service_main_pid,
        service_state_change_monotonic_usec=state_change_monotonic_usec,
        journal_event_id=journal_event.event_id,
        journal_entry_monotonic_usec=entry_timestamps,
        correlated_entry_monotonic_usec=tuple(correlated),
        exact_invocation_entry_monotonic_usec=tuple(exact),
    )


def _journal_entry_timestamps(event: SentinelEvent) -> tuple[int, ...]:
    raw_entries = event.attributes.get("entries")
    if not isinstance(raw_entries, Sequence) or isinstance(raw_entries, (str, bytes)):
        raise FaultLabTelemetryError("journal event entries must be a sequence")
    timestamps: list[int] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, Mapping):
            raise FaultLabTelemetryError("journal event entries must be mappings")
        value = raw_entry.get("monotonic_timestamp_usec")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise FaultLabTelemetryError(
                "journal entry monotonic timestamp must be non-negative"
            )
        timestamps.append(value)
    return tuple(timestamps)


def _required_text_attribute(attributes: Mapping[str, object], name: str) -> str:
    value = attributes.get(name)
    if not isinstance(value, str) or not value:
        raise FaultLabTelemetryError(f"service event {name} must be non-empty text")
    return value


def _optional_nonnegative_int_attribute(
    attributes: Mapping[str, object],
    name: str,
) -> int | None:
    value = attributes.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FaultLabTelemetryError(
            f"service event {name} must be a non-negative integer or None"
        )
    return value


def _timestamps_for_fault_samples(
    samples: Sequence[FaultExperimentTelemetrySample],
    *,
    selector: Callable[[FaultExperimentTelemetrySample], tuple[int, ...]],
    end_usec: int,
) -> tuple[int, ...]:
    values: list[int] = []
    for sample in samples:
        values.extend(value for value in selector(sample) if value <= end_usec)
    return tuple(sorted(values))


def _timestamps_at_or_after_transition(
    values: Sequence[int],
    *,
    transition_usec: int | None,
) -> tuple[int, ...]:
    if transition_usec is None:
        return ()
    return tuple(value for value in values if value >= transition_usec)


def _first_sample_latency(
    samples: Sequence[FaultExperimentTelemetrySample],
    *,
    reference_usec: int,
) -> int | None:
    if not samples:
        return None
    first = min(sample.observed_monotonic_usec for sample in samples)
    return first - reference_usec


def _first_timestamp_latency(
    values: Sequence[int],
    *,
    reference_usec: int | None,
) -> int | None:
    if not values or reference_usec is None:
        return None
    return min(values) - reference_usec


def _monotonic_usec(clock: MonotonicClock) -> int:
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FaultLabTelemetryError("monotonic clock must return a number")
    normalized = float(value)
    if normalized < 0.0:
        raise FaultLabTelemetryError("monotonic clock must not be negative")
    return int(normalized * 1_000_000)


def _validate_seconds(
    value: object,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
    allow_zero: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if allow_zero and normalized == 0.0:
        return normalized
    if normalized < minimum or normalized > maximum:
        raise ValueError(f"{field_name} must be between {minimum:g} and {maximum:g}")
    return normalized


def _validate_sample_bound(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_samples must be an integer")
    if not _MIN_MAX_SAMPLES <= value <= _MAX_MAX_SAMPLES:
        raise ValueError(
            f"max_samples must be between {_MIN_MAX_SAMPLES} and {_MAX_MAX_SAMPLES}"
        )
    return value


def _validate_nonnegative_int(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FaultLabExperimentContractError(
            f"{field_name} must be a non-negative integer"
        )


def _validate_nonempty_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise FaultLabExperimentContractError(f"{field_name} must be non-empty text")


def _validate_usec_tuple(values: object, *, field_name: str) -> None:
    if not isinstance(values, tuple):
        raise FaultLabExperimentContractError(f"{field_name} must be a tuple")
    previous: int | None = None
    for value in values:
        _validate_nonnegative_int(value, field_name=field_name)
        if previous is not None and value < previous:
            raise FaultLabExperimentContractError(
                f"{field_name} must be monotonic within one sample"
            )
        previous = value
