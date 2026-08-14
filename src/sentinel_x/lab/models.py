"""Typed experiment and ground-truth models for the Sentinel-X fault lab.

Phase 3A intentionally defines contracts only.  It does not execute fault
injection, send signals, or mutate service state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Final
from uuid import uuid4

from sentinel_x.systemd.models import validate_service_unit_name

_SCENARIO_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9._-]{2,63}$")
_EXPERIMENT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^exp-[0-9a-f]{32}$")
_MAX_DESCRIPTION_LENGTH: Final[int] = 512
_MAX_TIMEOUT_SECONDS: Final[float] = 300.0


class FaultLabModelError(ValueError):
    """Raised when a fault-laboratory model violates its contract."""


class FaultMode(StrEnum):
    """Observable service fault states supported by the first lab contract."""

    SERVICE_INACTIVE = "service_inactive"
    SERVICE_FAILED = "service_failed"


class FaultTemporalLabel(StrEnum):
    """Ground-truth temporal label relative to one injected fault window."""

    PRE_FAULT = "pre_fault"
    FAULT_ACTIVE = "fault_active"
    POST_FAULT = "post_fault"


def _utc_now() -> datetime:
    """Return one timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


def _new_experiment_id() -> str:
    """Return a collision-resistant experiment identifier."""

    return f"exp-{uuid4().hex}"


def _validate_scenario_id(value: str) -> str:
    if not isinstance(value, str):
        raise FaultLabModelError("scenario_id must be a string")
    if _SCENARIO_ID_PATTERN.fullmatch(value) is None:
        raise FaultLabModelError("scenario_id must match [a-z][a-z0-9._-]{2,63}")
    return value


def _validate_experiment_id(value: str) -> str:
    if not isinstance(value, str):
        raise FaultLabModelError("experiment_id must be a string")
    if _EXPERIMENT_ID_PATTERN.fullmatch(value) is None:
        raise FaultLabModelError("experiment_id must be exp- followed by 32 hex digits")
    return value


def _validate_description(value: str) -> str:
    if not isinstance(value, str):
        raise FaultLabModelError("description must be a string")
    if not value or value != value.strip():
        raise FaultLabModelError(
            "description must be non-empty and contain no surrounding whitespace"
        )
    if "\x00" in value:
        raise FaultLabModelError("description must not contain NUL bytes")
    if len(value) > _MAX_DESCRIPTION_LENGTH:
        raise FaultLabModelError("description is too long")
    return value


def _validate_timeout(value: float, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FaultLabModelError(f"{field_name} must be a positive number")
    normalized = float(value)
    if not 0.0 < normalized <= _MAX_TIMEOUT_SECONDS:
        raise FaultLabModelError(
            f"{field_name} must be greater than zero and at most "
            f"{_MAX_TIMEOUT_SECONDS:g} seconds"
        )
    return normalized


def _validate_aware_datetime(value: datetime, *, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise FaultLabModelError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise FaultLabModelError(f"{field_name} must be timezone-aware")


def _validate_monotonic_usec(value: int, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FaultLabModelError(f"{field_name} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class FaultScenario:
    """Immutable specification for one bounded systemd service fault scenario."""

    scenario_id: str
    description: str
    target_unit: str
    fault_mode: FaultMode
    baseline_timeout_seconds: float = 10.0
    fault_timeout_seconds: float = 10.0
    recovery_timeout_seconds: float = 20.0
    evidence_grace_seconds: float = 2.0

    def __post_init__(self) -> None:
        _validate_scenario_id(self.scenario_id)
        _validate_description(self.description)
        try:
            validate_service_unit_name(self.target_unit, field_name="target_unit")
        except ValueError as exc:
            raise FaultLabModelError(str(exc)) from exc
        if not isinstance(self.fault_mode, FaultMode):
            raise FaultLabModelError("fault_mode must be a FaultMode")

        for field_name in (
            "baseline_timeout_seconds",
            "fault_timeout_seconds",
            "recovery_timeout_seconds",
            "evidence_grace_seconds",
        ):
            normalized = _validate_timeout(
                getattr(self, field_name),
                field_name=field_name,
            )
            object.__setattr__(self, field_name, normalized)

        if self.evidence_grace_seconds > self.recovery_timeout_seconds:
            raise FaultLabModelError(
                "evidence_grace_seconds must not exceed recovery_timeout_seconds"
            )

    @property
    def expected_fault_active_states(self) -> tuple[str, ...]:
        """Return the service active states that establish this ground truth."""

        if self.fault_mode is FaultMode.SERVICE_INACTIVE:
            return ("inactive",)
        return ("failed",)

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly scenario representation."""

        return {
            "scenario_id": self.scenario_id,
            "description": self.description,
            "target_unit": self.target_unit,
            "fault_mode": self.fault_mode.value,
            "expected_fault_active_states": list(self.expected_fault_active_states),
            "baseline_timeout_seconds": self.baseline_timeout_seconds,
            "fault_timeout_seconds": self.fault_timeout_seconds,
            "recovery_timeout_seconds": self.recovery_timeout_seconds,
            "evidence_grace_seconds": self.evidence_grace_seconds,
        }


@dataclass(frozen=True, slots=True)
class FaultExperimentManifest:
    """Identity-bearing immutable manifest for one fault-lab experiment."""

    scenario: FaultScenario
    experiment_id: str = field(default_factory=_new_experiment_id)
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.scenario, FaultScenario):
            raise FaultLabModelError("scenario must be a FaultScenario")
        _validate_experiment_id(self.experiment_id)
        _validate_aware_datetime(self.created_at, field_name="created_at")

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly experiment manifest."""

        return {
            "experiment_id": self.experiment_id,
            "created_at": self.created_at.isoformat(),
            "scenario": self.scenario.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class FaultGroundTruthWindow:
    """Authoritative monotonic interval during which an injected fault existed."""

    experiment_id: str
    scenario_id: str
    target_unit: str
    fault_mode: FaultMode
    started_at: datetime
    started_monotonic_usec: int
    ended_at: datetime | None = None
    ended_monotonic_usec: int | None = None

    def __post_init__(self) -> None:
        _validate_experiment_id(self.experiment_id)
        _validate_scenario_id(self.scenario_id)
        try:
            validate_service_unit_name(self.target_unit, field_name="target_unit")
        except ValueError as exc:
            raise FaultLabModelError(str(exc)) from exc
        if not isinstance(self.fault_mode, FaultMode):
            raise FaultLabModelError("fault_mode must be a FaultMode")
        _validate_aware_datetime(self.started_at, field_name="started_at")
        _validate_monotonic_usec(
            self.started_monotonic_usec,
            field_name="started_monotonic_usec",
        )

        if (self.ended_at is None) != (self.ended_monotonic_usec is None):
            raise FaultLabModelError(
                "ended_at and ended_monotonic_usec must either both be set "
                "or both be None"
            )
        if self.ended_at is None or self.ended_monotonic_usec is None:
            return

        _validate_aware_datetime(self.ended_at, field_name="ended_at")
        _validate_monotonic_usec(
            self.ended_monotonic_usec,
            field_name="ended_monotonic_usec",
        )
        if self.ended_at < self.started_at:
            raise FaultLabModelError("ended_at must not precede started_at")
        if self.ended_monotonic_usec < self.started_monotonic_usec:
            raise FaultLabModelError(
                "ended_monotonic_usec must not precede started_monotonic_usec"
            )

    @classmethod
    def from_manifest(
        cls,
        manifest: FaultExperimentManifest,
        *,
        started_at: datetime,
        started_monotonic_usec: int,
    ) -> FaultGroundTruthWindow:
        """Open a ground-truth interval from a validated experiment manifest."""

        if not isinstance(manifest, FaultExperimentManifest):
            raise FaultLabModelError("manifest must be a FaultExperimentManifest")
        scenario = manifest.scenario
        return cls(
            experiment_id=manifest.experiment_id,
            scenario_id=scenario.scenario_id,
            target_unit=scenario.target_unit,
            fault_mode=scenario.fault_mode,
            started_at=started_at,
            started_monotonic_usec=started_monotonic_usec,
        )

    @property
    def is_open(self) -> bool:
        """Return whether recovery has not yet closed the truth interval."""

        return self.ended_monotonic_usec is None

    @property
    def duration_usec(self) -> int | None:
        """Return the closed fault duration in monotonic microseconds."""

        if self.ended_monotonic_usec is None:
            return None
        return self.ended_monotonic_usec - self.started_monotonic_usec

    def close(
        self,
        *,
        ended_at: datetime,
        ended_monotonic_usec: int,
    ) -> FaultGroundTruthWindow:
        """Return a closed copy without mutating the original truth window."""

        if not self.is_open:
            raise FaultLabModelError("ground-truth window is already closed")
        return replace(
            self,
            ended_at=ended_at,
            ended_monotonic_usec=ended_monotonic_usec,
        )

    def label_monotonic(self, monotonic_usec: int) -> FaultTemporalLabel:
        """Label one monotonic timestamp relative to the truth interval."""

        _validate_monotonic_usec(monotonic_usec, field_name="monotonic_usec")
        if monotonic_usec < self.started_monotonic_usec:
            return FaultTemporalLabel.PRE_FAULT
        if self.ended_monotonic_usec is None:
            return FaultTemporalLabel.FAULT_ACTIVE
        if monotonic_usec <= self.ended_monotonic_usec:
            return FaultTemporalLabel.FAULT_ACTIVE
        return FaultTemporalLabel.POST_FAULT

    def contains_monotonic(self, monotonic_usec: int) -> bool:
        """Return whether one monotonic timestamp lies inside the truth interval."""

        return self.label_monotonic(monotonic_usec) is FaultTemporalLabel.FAULT_ACTIVE

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly ground-truth representation."""

        return {
            "experiment_id": self.experiment_id,
            "scenario_id": self.scenario_id,
            "target_unit": self.target_unit,
            "fault_mode": self.fault_mode.value,
            "started_at": self.started_at.isoformat(),
            "started_monotonic_usec": self.started_monotonic_usec,
            "ended_at": None if self.ended_at is None else self.ended_at.isoformat(),
            "ended_monotonic_usec": self.ended_monotonic_usec,
            "is_open": self.is_open,
            "duration_usec": self.duration_usec,
        }
