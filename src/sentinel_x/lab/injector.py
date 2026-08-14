"""Constrained systemd fault injection for Sentinel-X laboratory fixtures.

Phase 3C is intentionally narrow.  It can mutate only a canonical volatile
``sentinel-x-lab-*.service`` fixture, records monotonic ground truth only after
the requested fault state is observed, and prepares recovery before mutation.
The adapter never invokes a shell and never performs privilege escalation.
"""

from __future__ import annotations

import hashlib
import stat
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Final, Protocol

from sentinel_x.lab.fixture import (
    SYSTEMD_LAB_RUNTIME_ROOT,
    SystemdLabFixtureArtifact,
    validate_lab_fixture_unit_name,
)
from sentinel_x.lab.models import (
    FaultExperimentManifest,
    FaultGroundTruthWindow,
    FaultMode,
)

_SYSTEMCTL: Final[str] = "/usr/bin/systemctl"
_DEFAULT_COMMAND_TIMEOUT_SECONDS: Final[float] = 5.0
_MIN_COMMAND_TIMEOUT_SECONDS: Final[float] = 0.1
_MAX_COMMAND_TIMEOUT_SECONDS: Final[float] = 30.0
_DEFAULT_POLL_INTERVAL_SECONDS: Final[float] = 0.05
_MIN_POLL_INTERVAL_SECONDS: Final[float] = 0.01
_MAX_POLL_INTERVAL_SECONDS: Final[float] = 1.0
_MAX_CAPTURE_BYTES: Final[int] = 65_536
_MAX_ERROR_CHARS: Final[int] = 512
_INSTALLED_UNIT_MODE: Final[int] = 0o644
_STATE_PROPERTIES: Final[tuple[str, ...]] = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "MainPID",
    "Result",
)


class FaultLabInjectionError(RuntimeError):
    """Base error for constrained fault-laboratory execution."""


class FaultLabPreconditionError(FaultLabInjectionError):
    """Raised before mutation when the laboratory safety contract is unmet."""


class FaultLabCommandError(FaultLabInjectionError):
    """Raised when a bounded systemctl operation fails."""


class FaultLabProtocolError(FaultLabInjectionError):
    """Raised when systemctl state output violates the expected contract."""


class FaultLabRecoveryError(FaultLabInjectionError):
    """Raised when recovery cannot restore the healthy fixture state."""


class LabFaultOperation(StrEnum):
    """Closed set of systemd mutations permitted by the Phase 3C injector."""

    STOP_SERVICE = "stop_service"
    ABORT_MAIN_PROCESS = "abort_main_process"


@dataclass(frozen=True, slots=True)
class SystemctlMutationResult:
    """Bounded raw result returned by one explicit systemctl operation."""

    returncode: int
    stdout: bytes
    stderr: bytes

    def __post_init__(self) -> None:
        if isinstance(self.returncode, bool) or not isinstance(self.returncode, int):
            raise TypeError("returncode must be an integer")
        if not isinstance(self.stdout, bytes):
            raise TypeError("stdout must be bytes")
        if not isinstance(self.stderr, bytes):
            raise TypeError("stderr must be bytes")


class SystemctlMutationRunner(Protocol):
    """Callable contract for one explicit bounded systemctl argv execution."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> SystemctlMutationResult:
        """Execute one command without a shell."""

        ...


class WallClock(Protocol):
    """Timezone-aware wall-clock provider."""

    def __call__(self) -> datetime:
        """Return one timezone-aware timestamp."""

        ...


class MonotonicClock(Protocol):
    """Monotonic-seconds provider."""

    def __call__(self) -> float:
        """Return monotonic seconds."""

        ...


class Sleeper(Protocol):
    """Bounded sleep provider."""

    def __call__(self, seconds: float) -> None:
        """Sleep for a non-negative bounded duration."""

        ...


class InstalledFixtureVerifier(Protocol):
    """Pre-mutation verifier for the installed volatile fixture artifact."""

    def __call__(self, artifact: SystemdLabFixtureArtifact) -> None:
        """Raise if the installed fixture no longer matches the artifact."""

        ...


@dataclass(frozen=True, slots=True)
class SystemdLabServiceState:
    """Minimal typed state required to verify injection and recovery."""

    unit_name: str
    load_state: str
    active_state: str
    sub_state: str
    main_pid: int
    result: str

    def __post_init__(self) -> None:
        validate_lab_fixture_unit_name(self.unit_name)
        for field_name in ("load_state", "active_state", "sub_state", "result"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or "\x00" in value:
                raise FaultLabProtocolError(
                    f"{field_name} must be a non-empty NUL-free string"
                )
        if (
            isinstance(self.main_pid, bool)
            or not isinstance(self.main_pid, int)
            or self.main_pid < 0
        ):
            raise FaultLabProtocolError("main_pid must be a non-negative integer")

    @property
    def is_healthy(self) -> bool:
        """Return whether the fixture is loaded with a live main process."""

        return (
            self.load_state == "loaded"
            and self.active_state == "active"
            and self.sub_state == "running"
            and self.main_pid > 0
        )

    def to_dict(self) -> dict[str, object]:
        """Return one serialization-friendly state representation."""

        return {
            "unit_name": self.unit_name,
            "load_state": self.load_state,
            "active_state": self.active_state,
            "sub_state": self.sub_state,
            "main_pid": self.main_pid,
            "result": self.result,
            "is_healthy": self.is_healthy,
        }


@dataclass(frozen=True, slots=True)
class SystemdLabFaultPlan:
    """Immutable recovery-first plan prepared before any systemd mutation."""

    experiment_id: str
    scenario_id: str
    target_unit: str
    fault_mode: FaultMode
    operation: LabFaultOperation
    expected_fault_active_states: tuple[str, ...]
    recovery_operations: tuple[str, ...]
    artifact_sha256: str

    def __post_init__(self) -> None:
        validate_lab_fixture_unit_name(self.target_unit)
        if not isinstance(self.fault_mode, FaultMode):
            raise FaultLabPreconditionError("fault_mode must be a FaultMode")
        if not isinstance(self.operation, LabFaultOperation):
            raise FaultLabPreconditionError("operation must be a LabFaultOperation")
        if not self.expected_fault_active_states:
            raise FaultLabPreconditionError(
                "expected_fault_active_states must not be empty"
            )
        if self.recovery_operations != ("start", "reset-failed"):
            raise FaultLabPreconditionError(
                "recovery_operations must be start followed by reset-failed"
            )
        if len(self.artifact_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.artifact_sha256
        ):
            raise FaultLabPreconditionError("artifact_sha256 must be lowercase SHA-256")

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly plan."""

        return {
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "target_unit": self.target_unit,
            "fault_mode": self.fault_mode.value,
            "operation": self.operation.value,
            "expected_fault_active_states": list(self.expected_fault_active_states),
            "recovery_operations": list(self.recovery_operations),
            "artifact_sha256": self.artifact_sha256,
        }


@dataclass(frozen=True, slots=True)
class FaultInjectionOutcome:
    """Closed ground truth and verified states from one recovered experiment."""

    plan: SystemdLabFaultPlan
    ground_truth: FaultGroundTruthWindow
    baseline_state: SystemdLabServiceState
    fault_state: SystemdLabServiceState
    recovered_state: SystemdLabServiceState

    def __post_init__(self) -> None:
        if self.ground_truth.is_open:
            raise FaultLabProtocolError("outcome ground truth must be closed")
        if self.ground_truth.experiment_id != self.plan.experiment_id:
            raise FaultLabProtocolError("ground truth experiment identity mismatch")
        if self.ground_truth.target_unit != self.plan.target_unit:
            raise FaultLabProtocolError("ground truth target identity mismatch")
        if not self.baseline_state.is_healthy:
            raise FaultLabProtocolError("baseline state must be healthy")
        if self.fault_state.active_state not in self.plan.expected_fault_active_states:
            raise FaultLabProtocolError("fault state does not satisfy the plan")
        if not self.recovered_state.is_healthy:
            raise FaultLabProtocolError("recovered state must be healthy")

    def to_dict(self) -> dict[str, object]:
        """Return stable experiment evidence without arbitrary command output."""

        return {
            "plan": self.plan.to_dict(),
            "ground_truth": self.ground_truth.to_dict(),
            "baseline_state": self.baseline_state.to_dict(),
            "fault_state": self.fault_state.to_dict(),
            "recovered_state": self.recovered_state.to_dict(),
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _command_environment() -> dict[str, str]:
    return {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "SYSTEMD_COLORS": "0",
        "SYSTEMD_PAGER": "cat",
        "PAGER": "cat",
    }


def _default_runner(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
) -> SystemctlMutationResult:
    try:
        completed = subprocess.run(
            tuple(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            shell=False,
            timeout=timeout_seconds,
            env=_command_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise FaultLabCommandError(
            f"systemctl operation exceeded {timeout_seconds:.3f} seconds"
        ) from exc
    except OSError as exc:
        raise FaultLabCommandError(f"systemctl execution failed: {exc}") from exc

    return SystemctlMutationResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _validate_positive_seconds(
    value: float,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FaultLabPreconditionError(f"{field_name} must be a number")
    normalized = float(value)
    if not minimum <= normalized <= maximum:
        raise FaultLabPreconditionError(
            f"{field_name} must be between {minimum:g} and {maximum:g} seconds"
        )
    return normalized


def _bounded_capture(value: bytes, *, stream_name: str) -> str:
    if len(value) > _MAX_CAPTURE_BYTES:
        raise FaultLabProtocolError(
            f"systemctl {stream_name} exceeded {_MAX_CAPTURE_BYTES} bytes"
        )
    if b"\x00" in value:
        raise FaultLabProtocolError(f"systemctl {stream_name} contains a NUL byte")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FaultLabProtocolError(
            f"systemctl {stream_name} is not valid UTF-8"
        ) from exc


def _bounded_error_text(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= _MAX_ERROR_CHARS:
        return normalized
    return normalized[: _MAX_ERROR_CHARS - 3] + "..."


def _monotonic_usec(clock: MonotonicClock) -> int:
    value = clock()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FaultLabProtocolError("monotonic clock must return a number")
    normalized = float(value)
    if normalized < 0.0:
        raise FaultLabProtocolError("monotonic clock must not be negative")
    return int(normalized * 1_000_000)


def _validate_wall_clock(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise FaultLabProtocolError("wall clock must return a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FaultLabProtocolError("wall clock must return an aware datetime")
    return value


def verify_installed_lab_fixture(artifact: SystemdLabFixtureArtifact) -> None:
    """Verify root-owned canonical bytes at the exact volatile fixture path."""

    if not isinstance(artifact, SystemdLabFixtureArtifact):
        raise FaultLabPreconditionError("artifact must be a SystemdLabFixtureArtifact")
    validate_lab_fixture_unit_name(artifact.unit_name)
    expected_path = SYSTEMD_LAB_RUNTIME_ROOT / artifact.unit_name
    if artifact.runtime_install_path != expected_path:
        raise FaultLabPreconditionError("fixture runtime path is not canonical")

    try:
        metadata = expected_path.lstat()
    except OSError as exc:
        raise FaultLabPreconditionError(
            f"installed fixture cannot be inspected: {exc}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise FaultLabPreconditionError(
            "installed fixture must be a regular non-symlink file"
        )
    if metadata.st_uid != 0 or metadata.st_gid != 0:
        raise FaultLabPreconditionError("installed fixture must be owned by root:root")
    if stat.S_IMODE(metadata.st_mode) != _INSTALLED_UNIT_MODE:
        raise FaultLabPreconditionError("installed fixture mode must be 0644")

    expected_bytes = artifact.unit_text.encode("utf-8")
    if metadata.st_size != len(expected_bytes):
        raise FaultLabPreconditionError("installed fixture size mismatch")
    try:
        installed_bytes = expected_path.read_bytes()
    except OSError as exc:
        raise FaultLabPreconditionError(
            f"installed fixture cannot be read: {exc}"
        ) from exc
    if installed_bytes != expected_bytes:
        raise FaultLabPreconditionError("installed fixture bytes are not canonical")
    installed_sha256 = hashlib.sha256(installed_bytes).hexdigest()
    if installed_sha256 != artifact.sha256:
        raise FaultLabPreconditionError("installed fixture SHA-256 mismatch")


def _parse_state(unit_name: str, stdout: str) -> SystemdLabServiceState:
    properties: dict[str, str] = {}
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line:
            continue
        if "=" not in line:
            raise FaultLabProtocolError(
                f"systemctl show line {line_number} is not an assignment"
            )
        key, value = line.split("=", 1)
        if key in properties:
            raise FaultLabProtocolError(f"duplicate systemctl property: {key}")
        properties[key] = value

    missing = sorted(set(_STATE_PROPERTIES) - properties.keys())
    if missing:
        raise FaultLabProtocolError(
            "systemctl show is missing properties: " + ", ".join(missing)
        )
    if properties["Id"] != unit_name:
        raise FaultLabProtocolError("systemctl returned an unexpected unit identity")
    try:
        main_pid = int(properties["MainPID"], 10)
    except ValueError as exc:
        raise FaultLabProtocolError("MainPID must be an integer") from exc
    return SystemdLabServiceState(
        unit_name=unit_name,
        load_state=properties["LoadState"],
        active_state=properties["ActiveState"],
        sub_state=properties["SubState"],
        main_pid=main_pid,
        result=properties["Result"],
    )


def _plan_from_manifest(
    manifest: FaultExperimentManifest,
    artifact: SystemdLabFixtureArtifact,
) -> SystemdLabFaultPlan:
    if not isinstance(manifest, FaultExperimentManifest):
        raise FaultLabPreconditionError("manifest must be a FaultExperimentManifest")
    if not isinstance(artifact, SystemdLabFixtureArtifact):
        raise FaultLabPreconditionError("artifact must be a SystemdLabFixtureArtifact")
    scenario = manifest.scenario
    if scenario.target_unit != artifact.unit_name:
        raise FaultLabPreconditionError(
            "scenario target_unit must equal the canonical fixture unit"
        )
    validate_lab_fixture_unit_name(scenario.target_unit)

    if scenario.fault_mode is FaultMode.SERVICE_INACTIVE:
        operation = LabFaultOperation.STOP_SERVICE
    elif scenario.fault_mode is FaultMode.SERVICE_FAILED:
        operation = LabFaultOperation.ABORT_MAIN_PROCESS
    else:
        raise FaultLabPreconditionError("unsupported fault mode")

    return SystemdLabFaultPlan(
        experiment_id=manifest.experiment_id,
        scenario_id=scenario.scenario_id,
        target_unit=scenario.target_unit,
        fault_mode=scenario.fault_mode,
        operation=operation,
        expected_fault_active_states=scenario.expected_fault_active_states,
        recovery_operations=("start", "reset-failed"),
        artifact_sha256=artifact.sha256,
    )


class SystemdLabFaultInjector:
    """Recovery-first injector restricted to one canonical Sentinel-X fixture."""

    def __init__(
        self,
        *,
        runner: SystemctlMutationRunner = _default_runner,
        install_verifier: InstalledFixtureVerifier = verify_installed_lab_fixture,
        wall_clock: WallClock = _utc_now,
        monotonic_clock: MonotonicClock = time.monotonic,
        sleeper: Sleeper = _sleep,
        command_timeout_seconds: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if not callable(runner):
            raise TypeError("runner must be callable")
        if not callable(install_verifier):
            raise TypeError("install_verifier must be callable")
        if not callable(wall_clock):
            raise TypeError("wall_clock must be callable")
        if not callable(monotonic_clock):
            raise TypeError("monotonic_clock must be callable")
        if not callable(sleeper):
            raise TypeError("sleeper must be callable")
        self._runner = runner
        self._install_verifier = install_verifier
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._sleeper = sleeper
        self._command_timeout_seconds = _validate_positive_seconds(
            command_timeout_seconds,
            field_name="command_timeout_seconds",
            minimum=_MIN_COMMAND_TIMEOUT_SECONDS,
            maximum=_MAX_COMMAND_TIMEOUT_SECONDS,
        )
        self._poll_interval_seconds = _validate_positive_seconds(
            poll_interval_seconds,
            field_name="poll_interval_seconds",
            minimum=_MIN_POLL_INTERVAL_SECONDS,
            maximum=_MAX_POLL_INTERVAL_SECONDS,
        )

    def prepare(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> SystemdLabFaultPlan:
        """Validate identity and installed bytes before exposing a fault plan."""

        plan = _plan_from_manifest(manifest, artifact)
        self._install_verifier(artifact)
        baseline = self._read_state(plan.target_unit)
        if not baseline.is_healthy:
            raise FaultLabPreconditionError(
                "fixture must be active/running with a live MainPID before injection"
            )
        return plan

    def execute(
        self,
        manifest: FaultExperimentManifest,
        artifact: SystemdLabFixtureArtifact,
    ) -> FaultInjectionOutcome:
        """Inject one bounded fault, preserve evidence grace, and recover."""

        plan = self.prepare(manifest, artifact)
        baseline_state = self._read_state(plan.target_unit)
        if not baseline_state.is_healthy:
            raise FaultLabPreconditionError(
                "fixture stopped being healthy after plan preparation"
            )

        self._install_verifier(artifact)
        mutation_attempted = False
        open_truth: FaultGroundTruthWindow | None = None
        fault_state: SystemdLabServiceState | None = None

        try:
            mutation_attempted = True
            self._apply_fault(plan)
            fault_state = self._wait_for_fault_state(
                plan,
                timeout_seconds=manifest.scenario.fault_timeout_seconds,
            )
            open_truth = FaultGroundTruthWindow.from_manifest(
                manifest,
                started_at=_validate_wall_clock(self._wall_clock()),
                started_monotonic_usec=_monotonic_usec(self._monotonic_clock),
            )
            self._sleeper(manifest.scenario.evidence_grace_seconds)
        except BaseException as primary_error:
            if mutation_attempted:
                self._recover_or_raise(
                    plan.target_unit,
                    manifest.scenario.recovery_timeout_seconds,
                    primary_error=primary_error,
                )
            raise

        recovered_state = self._recover_or_raise(
            plan.target_unit,
            manifest.scenario.recovery_timeout_seconds,
            primary_error=None,
        )
        if open_truth is None or fault_state is None:
            raise FaultLabProtocolError(
                "fault execution did not establish ground truth"
            )
        closed_truth = open_truth.close(
            ended_at=_validate_wall_clock(self._wall_clock()),
            ended_monotonic_usec=_monotonic_usec(self._monotonic_clock),
        )
        return FaultInjectionOutcome(
            plan=plan,
            ground_truth=closed_truth,
            baseline_state=baseline_state,
            fault_state=fault_state,
            recovered_state=recovered_state,
        )

    def _run(self, *arguments: str) -> str:
        argv = (
            _SYSTEMCTL,
            "--system",
            "--no-ask-password",
            "--no-pager",
            *arguments,
        )
        result = self._runner(
            argv,
            timeout_seconds=self._command_timeout_seconds,
        )
        stdout = _bounded_capture(result.stdout, stream_name="stdout")
        stderr = _bounded_capture(result.stderr, stream_name="stderr")
        if result.returncode != 0:
            detail = _bounded_error_text(stderr or stdout or "no command output")
            raise FaultLabCommandError(
                f"systemctl operation failed with status {result.returncode}: {detail}"
            )
        return stdout

    def _read_state(self, unit_name: str) -> SystemdLabServiceState:
        validate_lab_fixture_unit_name(unit_name)
        stdout = self._run(
            "show",
            f"--property={','.join(_STATE_PROPERTIES)}",
            unit_name,
        )
        return _parse_state(unit_name, stdout)

    def _apply_fault(self, plan: SystemdLabFaultPlan) -> None:
        validate_lab_fixture_unit_name(plan.target_unit)
        if plan.operation is LabFaultOperation.STOP_SERVICE:
            self._run("stop", plan.target_unit)
            return
        if plan.operation is LabFaultOperation.ABORT_MAIN_PROCESS:
            self._run(
                "kill",
                "--kill-whom=main",
                "--signal=SIGKILL",
                plan.target_unit,
            )
            return
        raise FaultLabPreconditionError("unsupported fault operation")

    def _wait_for_fault_state(
        self,
        plan: SystemdLabFaultPlan,
        *,
        timeout_seconds: float,
    ) -> SystemdLabServiceState:
        deadline = self._deadline(timeout_seconds)
        while True:
            state = self._read_state(plan.target_unit)
            if state.active_state in plan.expected_fault_active_states:
                return state
            if self._monotonic_seconds() >= deadline:
                raise FaultLabCommandError(
                    "fault state was not observed before the bounded timeout"
                )
            self._sleeper(self._poll_interval_seconds)

    def _recover_or_raise(
        self,
        unit_name: str,
        timeout_seconds: float,
        *,
        primary_error: BaseException | None,
    ) -> SystemdLabServiceState:
        try:
            self._run("start", unit_name)
            self._run("reset-failed", unit_name)
            return self._wait_for_healthy_state(
                unit_name,
                timeout_seconds=timeout_seconds,
            )
        except BaseException as recovery_error:
            message = "fixture recovery failed after fault mutation"
            if primary_error is not None:
                message += f"; original error: {type(primary_error).__name__}"
            raise FaultLabRecoveryError(message) from recovery_error

    def _wait_for_healthy_state(
        self,
        unit_name: str,
        *,
        timeout_seconds: float,
    ) -> SystemdLabServiceState:
        deadline = self._deadline(timeout_seconds)
        while True:
            state = self._read_state(unit_name)
            if state.is_healthy:
                return state
            if self._monotonic_seconds() >= deadline:
                raise FaultLabCommandError(
                    "healthy recovery state was not observed before timeout"
                )
            self._sleeper(self._poll_interval_seconds)

    def _deadline(self, timeout_seconds: float) -> float:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise FaultLabPreconditionError("timeout_seconds must be a number")
        normalized = float(timeout_seconds)
        if normalized <= 0.0 or normalized > 300.0:
            raise FaultLabPreconditionError(
                "timeout_seconds must be greater than zero and at most 300"
            )
        return self._monotonic_seconds() + normalized

    def _monotonic_seconds(self) -> float:
        value = self._monotonic_clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FaultLabProtocolError("monotonic clock must return a number")
        normalized = float(value)
        if normalized < 0.0:
            raise FaultLabProtocolError("monotonic clock must not be negative")
        return normalized
