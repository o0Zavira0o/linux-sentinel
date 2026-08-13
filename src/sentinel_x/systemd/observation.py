"""Configured systemd service observation collectors for Sentinel-X."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from sentinel_x.core.events import EventKind, SentinelEvent
from sentinel_x.systemd.models import (
    SystemdServiceSnapshot,
    validate_service_unit_name,
)
from sentinel_x.systemd.reader import SystemctlServiceReader

SYSTEMD_SERVICE_OBSERVATION_SOURCE: Final[str] = "sentinel_x.systemd.service"
SYSTEMD_SERVICE_OBSERVATION_TYPE: Final[str] = "linux.systemd.service"
_COLLECTOR_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
)


class SystemdServiceCollectorError(RuntimeError):
    """Base error for configured systemd service collector construction."""


class SystemdServiceCollectorBindingError(SystemdServiceCollectorError):
    """Raised when configured collector bindings are ambiguous or invalid."""


class SystemdServiceSnapshotReader(Protocol):
    """Structural read-only service snapshot dependency."""

    def read_service(self, unit_name: str) -> SystemdServiceSnapshot:
        """Return one current systemd service snapshot."""

        ...


@dataclass(frozen=True, slots=True)
class SystemdServiceEmission:
    """Runtime emission returned by one systemd service collector."""

    collector_name: str
    event: SentinelEvent

    def __post_init__(self) -> None:
        """Validate the runtime emission contract."""

        _validate_collector_name(self.collector_name)
        if not isinstance(self.event, SentinelEvent):
            raise TypeError("event must be a SentinelEvent")

    def to_dict(self) -> dict[str, object]:
        """Return bounded metadata without duplicating event attributes."""

        return {
            "collector_name": self.collector_name,
            "event_id": self.event.event_id,
            "event_kind": self.event.kind.value,
            "event_source": self.event.source,
        }


def systemd_service_snapshot_to_event(
    snapshot: SystemdServiceSnapshot,
    *,
    collector_name: str,
) -> SentinelEvent:
    """Convert one typed systemd service snapshot into a SentinelEvent."""

    if not isinstance(snapshot, SystemdServiceSnapshot):
        raise TypeError("snapshot must be a SystemdServiceSnapshot")
    collector_name = _validate_collector_name(collector_name)

    attributes = snapshot.to_dict()
    attributes["observation_type"] = SYSTEMD_SERVICE_OBSERVATION_TYPE
    attributes["collector_name"] = collector_name

    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_SERVICE_OBSERVATION_SOURCE,
        message=(
            f"systemd service observation collected for {snapshot.requested_name}."
        ),
        attributes=attributes,
        occurred_at=snapshot.captured_at,
    )


class SystemdServiceCollector:
    """Read and emit one configured systemd service target."""

    def __init__(
        self,
        collector_name: str,
        unit_name: str,
        *,
        reader: SystemdServiceSnapshotReader | None = None,
    ) -> None:
        self._collector_name = _validate_collector_name(collector_name)
        self._unit_name = validate_service_unit_name(
            unit_name,
            field_name="unit_name",
        )
        self._reader: SystemdServiceSnapshotReader = (
            SystemctlServiceReader() if reader is None else reader
        )

    @property
    def name(self) -> str:
        """Return the scheduler identity for this configured target."""

        return self._collector_name

    @property
    def unit_name(self) -> str:
        """Return the requested systemd service unit name."""

        return self._unit_name

    def collect(self) -> SystemdServiceEmission:
        """Read one service snapshot and emit its typed observation event."""

        snapshot = self._reader.read_service(self._unit_name)
        event = systemd_service_snapshot_to_event(
            snapshot,
            collector_name=self._collector_name,
        )
        return SystemdServiceEmission(
            collector_name=self._collector_name,
            event=event,
        )


class ConfiguredSystemdServiceCollectors:
    """Bounded trusted handler set for configured systemd service targets."""

    def __init__(
        self,
        bindings: Iterable[tuple[str, str]],
        *,
        reader: SystemdServiceSnapshotReader | None = None,
    ) -> None:
        normalized = tuple(bindings)
        if not normalized:
            self._collectors: tuple[SystemdServiceCollector, ...] = ()
            return

        collector_names: list[str] = []
        unit_names: list[str] = []
        collectors: list[SystemdServiceCollector] = []
        shared_reader: SystemdServiceSnapshotReader = (
            SystemctlServiceReader() if reader is None else reader
        )

        for binding in normalized:
            if not isinstance(binding, tuple) or len(binding) != 2:
                raise SystemdServiceCollectorBindingError(
                    "systemd service bindings must be "
                    "(collector_name, unit_name) tuples"
                )
            collector_name, unit_name = binding
            collector = SystemdServiceCollector(
                collector_name,
                unit_name,
                reader=shared_reader,
            )
            collector_names.append(collector.name)
            unit_names.append(collector.unit_name)
            collectors.append(collector)

        if len(set(collector_names)) != len(collector_names):
            raise SystemdServiceCollectorBindingError(
                "systemd service bindings contain duplicate collector names"
            )
        if len(set(unit_names)) != len(unit_names):
            raise SystemdServiceCollectorBindingError(
                "systemd service bindings contain duplicate unit names"
            )

        self._collectors = tuple(collectors)

    def collectors(self) -> tuple[SystemdServiceCollector, ...]:
        """Return configured collectors in declared binding order."""

        return self._collectors

    def handlers(self) -> Mapping[str, Callable[[], object]]:
        """Return the exact trusted handler mapping consumed by the registry."""

        return {collector.name: collector.collect for collector in self._collectors}


def _validate_collector_name(value: object) -> str:
    """Validate one scheduler-compatible collector identity."""

    if not isinstance(value, str):
        raise SystemdServiceCollectorBindingError("collector_name must be a string")
    normalized = value.strip()
    if _COLLECTOR_NAME_PATTERN.fullmatch(normalized) is None:
        raise SystemdServiceCollectorBindingError(
            "collector_name must be 1-64 scheduler-safe characters"
        )
    return normalized
