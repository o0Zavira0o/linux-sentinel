"""Bounded Linux-native systemd dependency discovery for Sentinel-X Phase 5B.

Phase 5B reads dependency properties from the running systemd manager and
walks only the requirement relations currently contracted by Phase 5A.
Ordering relations are observed but are not used as traversal edges.  The
result is explicitly scope-bounded and non-causal: it does not claim that the
observed topology is a complete systemd graph or that any dependency caused a
fault.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Protocol

from sentinel_x.core.events import EventKind, EventSeverity, SentinelEvent
from sentinel_x.dependency.models import (
    DependencyConfigurationOrigin,
    DependencyEndpoint,
    DependencyEntityKind,
    DependencyEvidence,
    DependencyEvidenceOrigin,
    DependencyModelError,
    DependencyRelation,
    source_property_for_relation,
)
from sentinel_x.systemd.boot import (
    SystemBootIdError,
    normalize_boot_id,
    read_current_boot_id,
)
from sentinel_x.systemd.reader import SystemctlCommandResult

SYSTEMD_DEPENDENCY_DISCOVERY_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.systemd-dependency-discovery.v1"
)
SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE: Final[str] = "sentinel_x.dependency.discovery"
SYSTEMD_DEPENDENCY_OBSERVATION_TYPE: Final[str] = "linux.systemd.unit.dependency"

_DEFAULT_TIMEOUT_SECONDS: Final[float] = 3.0
_MIN_TIMEOUT_SECONDS: Final[float] = 0.1
_MAX_TIMEOUT_SECONDS: Final[float] = 30.0
_MAX_CAPTURE_BYTES: Final[int] = 65_536
_MAX_ERROR_MESSAGE_CHARS: Final[int] = 512
_MAX_UNIT_NAME_LENGTH: Final[int] = 255
_MAX_PROPERTY_ENTRIES: Final[int] = 4096
_MAX_DISCOVERY_DEPTH: Final[int] = 8
_MAX_DISCOVERY_UNITS: Final[int] = 1024

_SYSTEMCTL_PROPERTIES: Final[tuple[str, ...]] = (
    "Id",
    "Names",
    "LoadState",
    "Requires",
    "Wants",
    "After",
    "Before",
)
_REQUIRED_PROPERTIES: Final[frozenset[str]] = frozenset(_SYSTEMCTL_PROPERTIES)
_RELATION_FIELDS: Final[tuple[tuple[DependencyRelation, str], ...]] = (
    (DependencyRelation.REQUIRES, "requires"),
    (DependencyRelation.WANTS, "wants"),
    (DependencyRelation.AFTER, "after"),
    (DependencyRelation.BEFORE, "before"),
)
_TRAVERSAL_RELATIONS: Final[frozenset[DependencyRelation]] = frozenset(
    {
        DependencyRelation.REQUIRES,
        DependencyRelation.WANTS,
    }
)
_EVIDENCE_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.systemd-discovered-dependency-evidence.v1\x00"
)


class SystemdDependencyDiscoveryError(RuntimeError):
    """Base error for Phase 5B dependency discovery."""


class SystemdDependencyReadError(SystemdDependencyDiscoveryError):
    """Raised when one read-only systemd dependency query fails."""


class SystemdDependencyExecutableNotFoundError(SystemdDependencyReadError):
    """Raised when systemctl cannot be resolved."""


class SystemdDependencyCommandError(SystemdDependencyReadError):
    """Raised when systemctl cannot complete a bounded read."""


class SystemdDependencyCommandTimeoutError(SystemdDependencyCommandError):
    """Raised when a systemctl read exceeds its timeout."""


class SystemdDependencyProtocolError(SystemdDependencyReadError):
    """Raised when systemctl output violates the discovery protocol."""


class SystemdDependencyDiscoveryCapacityError(SystemdDependencyDiscoveryError):
    """Raised instead of silently truncating the unit frontier by capacity."""


class DependencyCommandRunner(Protocol):
    """Callable contract for one explicit systemctl invocation."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> SystemctlCommandResult:
        """Execute a bounded command without a shell."""

        ...


class DependencyUnitReader(Protocol):
    """Reader contract used by bounded dependency traversal."""

    def read_unit(self, unit_name: str) -> SystemdDependencyUnitSnapshot:
        """Read one generic systemd unit dependency snapshot."""

        ...


class Clock(Protocol):
    """Timezone-aware clock contract."""

    def __call__(self) -> datetime:
        """Return the current wall-clock time."""

        ...


class EventIdFactory(Protocol):
    """Factory for source observation event identities."""

    def __call__(self) -> str:
        """Return one non-empty event identity."""

        ...


@dataclass(frozen=True, slots=True)
class SystemdDependencyUnitSnapshot:
    """One generic systemd-unit dependency snapshot read from the manager."""

    requested_name: str
    canonical_name: str
    names: tuple[str, ...]
    load_state: str
    requires: tuple[str, ...]
    wants: tuple[str, ...]
    after: tuple[str, ...]
    before: tuple[str, ...]
    captured_at: datetime

    def __post_init__(self) -> None:
        """Validate the manager snapshot without narrowing to service units."""

        validate_systemd_unit_identity(self.requested_name, field_name="requested_name")
        canonical = validate_systemd_unit_identity(
            self.canonical_name,
            field_name="canonical_name",
        )
        _validate_unit_tuple(self.names, field_name="names")
        if canonical not in self.names:
            raise SystemdDependencyProtocolError("names must include canonical_name")
        _validate_nonempty_text(self.load_state, field_name="load_state")
        for field_name, values in (
            ("requires", self.requires),
            ("wants", self.wants),
            ("after", self.after),
            ("before", self.before),
        ):
            _validate_unit_tuple(values, field_name=field_name)
        _validate_aware_datetime(self.captured_at, field_name="captured_at")

    def to_dict(self) -> dict[str, object]:
        """Return a stable serialization-friendly snapshot."""

        return {
            "requested_name": self.requested_name,
            "canonical_name": self.canonical_name,
            "names": list(self.names),
            "load_state": self.load_state,
            "requires": list(self.requires),
            "wants": list(self.wants),
            "after": list(self.after),
            "before": list(self.before),
            "captured_at": self.captured_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class DiscoveredSystemdUnit:
    """One successfully observed unit and its non-causal evidence edges."""

    depth: int
    snapshot: SystemdDependencyUnitSnapshot
    source_event: SentinelEvent
    evidence: tuple[DependencyEvidence, ...]

    def __post_init__(self) -> None:
        if (
            isinstance(self.depth, bool)
            or not isinstance(self.depth, int)
            or self.depth < 0
        ):
            raise SystemdDependencyDiscoveryError(
                "depth must be a non-negative integer"
            )
        if not isinstance(self.snapshot, SystemdDependencyUnitSnapshot):
            raise SystemdDependencyDiscoveryError(
                "snapshot must be a SystemdDependencyUnitSnapshot"
            )
        if not isinstance(self.source_event, SentinelEvent):
            raise SystemdDependencyDiscoveryError(
                "source_event must be a SentinelEvent"
            )
        if self.source_event.kind is not EventKind.OBSERVATION:
            raise SystemdDependencyDiscoveryError("source_event must be an observation")
        if self.source_event.source != SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE:
            raise SystemdDependencyDiscoveryError(
                "source_event has an unexpected source"
            )
        if not isinstance(self.evidence, tuple):
            raise SystemdDependencyDiscoveryError("evidence must be a tuple")
        expected_subject = DependencyEndpoint(
            kind=DependencyEntityKind.SYSTEMD_UNIT,
            identity=self.snapshot.canonical_name,
        )
        evidence_ids: set[str] = set()
        for item in self.evidence:
            if not isinstance(item, DependencyEvidence):
                raise SystemdDependencyDiscoveryError(
                    "evidence must contain DependencyEvidence values"
                )
            if item.evidence_id in evidence_ids:
                raise SystemdDependencyDiscoveryError(
                    "evidence must not contain duplicate identities"
                )
            evidence_ids.add(item.evidence_id)
            if item.source_event_id != self.source_event.event_id:
                raise SystemdDependencyDiscoveryError(
                    "evidence source_event_id must match source_event"
                )
            if item.subject != expected_subject:
                raise SystemdDependencyDiscoveryError(
                    "evidence subject must match snapshot canonical_name"
                )

    def to_dict(self) -> dict[str, object]:
        """Return a bounded serialization-friendly discovered-unit record."""

        return {
            "depth": self.depth,
            "snapshot": self.snapshot.to_dict(),
            "source_event": self.source_event.to_dict(),
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class SystemdDependencyDiscoveryFailure:
    """Explicit evidence gap for one dependency unit that could not be read."""

    requested_unit: str
    depth: int
    error_type: str
    message: str

    def __post_init__(self) -> None:
        validate_systemd_unit_identity(self.requested_unit, field_name="requested_unit")
        if (
            isinstance(self.depth, bool)
            or not isinstance(self.depth, int)
            or self.depth < 0
        ):
            raise SystemdDependencyDiscoveryError(
                "depth must be a non-negative integer"
            )
        _validate_nonempty_text(self.error_type, field_name="error_type")
        _validate_nonempty_text(self.message, field_name="message")
        if len(self.message) > _MAX_ERROR_MESSAGE_CHARS:
            raise SystemdDependencyDiscoveryError("failure message is too long")

    def to_dict(self) -> dict[str, object]:
        return {
            "requested_unit": self.requested_unit,
            "depth": self.depth,
            "error_type": self.error_type,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class SystemdDependencyDiscoveryReport:
    """Bounded Phase 5B discovery report with explicit incompleteness."""

    root_requested_unit: str
    root_canonical_unit: str
    boot_id: str
    max_depth: int
    max_units: int
    units: tuple[DiscoveredSystemdUnit, ...]
    failures: tuple[SystemdDependencyDiscoveryFailure, ...]
    truncated_by_depth: bool
    unexpanded_requirement_count: int

    def __post_init__(self) -> None:
        validate_systemd_unit_identity(
            self.root_requested_unit,
            field_name="root_requested_unit",
        )
        validate_systemd_unit_identity(
            self.root_canonical_unit,
            field_name="root_canonical_unit",
        )
        _normalize_boot_id(self.boot_id)
        _validate_discovery_bounds(self.max_depth, self.max_units)
        if not isinstance(self.units, tuple):
            raise SystemdDependencyDiscoveryError("units must be a tuple")
        if not self.units:
            raise SystemdDependencyDiscoveryError(
                "units must contain the root observation"
            )
        if len(self.units) > self.max_units:
            raise SystemdDependencyDiscoveryError("units exceed max_units")
        if not isinstance(self.failures, tuple):
            raise SystemdDependencyDiscoveryError("failures must be a tuple")
        if type(self.truncated_by_depth) is not bool:
            raise SystemdDependencyDiscoveryError(
                "truncated_by_depth must be a boolean"
            )
        if (
            isinstance(self.unexpanded_requirement_count, bool)
            or not isinstance(self.unexpanded_requirement_count, int)
            or self.unexpanded_requirement_count < 0
        ):
            raise SystemdDependencyDiscoveryError(
                "unexpanded_requirement_count must be a non-negative integer"
            )
        if self.truncated_by_depth != (self.unexpanded_requirement_count > 0):
            raise SystemdDependencyDiscoveryError(
                "depth truncation flag must match unexpanded requirement count"
            )
        root = self.units[0]
        if root.depth != 0:
            raise SystemdDependencyDiscoveryError(
                "first discovered unit must be the root"
            )
        if root.snapshot.canonical_name != self.root_canonical_unit:
            raise SystemdDependencyDiscoveryError(
                "root_canonical_unit must match the root snapshot"
            )

    @property
    def complete_within_scope(self) -> bool:
        """Whether every in-scope frontier read completed within configured bounds."""

        return not self.failures and not self.truncated_by_depth

    @property
    def evidence_count(self) -> int:
        return sum(len(unit.evidence) for unit in self.units)

    def to_dict(self) -> dict[str, object]:
        """Serialize scope and uncertainty instead of implying graph completeness."""

        return {
            "schema_version": SYSTEMD_DEPENDENCY_DISCOVERY_SCHEMA_VERSION,
            "root_requested_unit": self.root_requested_unit,
            "root_canonical_unit": self.root_canonical_unit,
            "boot_id": self.boot_id,
            "max_depth": self.max_depth,
            "max_units": self.max_units,
            "observed_relations": [
                relation.value for relation, _field_name in _RELATION_FIELDS
            ],
            "traversal_relations": [
                DependencyRelation.REQUIRES.value,
                DependencyRelation.WANTS.value,
            ],
            "comprehensive_systemd_relation_scope": False,
            "causal_claims_assigned": False,
            "complete_within_scope": self.complete_within_scope,
            "truncated_by_depth": self.truncated_by_depth,
            "unexpanded_requirement_count": self.unexpanded_requirement_count,
            "unit_count": len(self.units),
            "evidence_count": self.evidence_count,
            "failure_count": len(self.failures),
            "units": [unit.to_dict() for unit in self.units],
            "failures": [failure.to_dict() for failure in self.failures],
        }


class SystemctlDependencyUnitReader:
    """Read generic systemd-unit dependency properties without lifecycle mutation."""

    def __init__(
        self,
        *,
        systemctl_path: str | Path | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        runner: DependencyCommandRunner | None = None,
        clock: Clock | None = None,
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds,
            (int, float),
        ):
            raise TypeError("timeout_seconds must be a real number")
        normalized_timeout = float(timeout_seconds)
        if not _MIN_TIMEOUT_SECONDS <= normalized_timeout <= _MAX_TIMEOUT_SECONDS:
            raise ValueError(
                "timeout_seconds must be between "
                f"{_MIN_TIMEOUT_SECONDS} and {_MAX_TIMEOUT_SECONDS} seconds"
            )

        if systemctl_path is None:
            resolved = shutil.which("systemctl")
            if resolved is None:
                raise SystemdDependencyExecutableNotFoundError(
                    "systemctl executable was not found in PATH"
                )
            normalized_path = resolved
        else:
            normalized_path = os.fspath(systemctl_path)
            if not normalized_path or "\x00" in normalized_path:
                raise ValueError("systemctl_path must be non-empty and contain no NUL")

        self._systemctl_path = normalized_path
        self._timeout_seconds = normalized_timeout
        self._runner = _default_runner if runner is None else runner
        self._clock: Clock = _utc_now if clock is None else clock

    def read_unit(self, unit_name: str) -> SystemdDependencyUnitSnapshot:
        """Read one generic unit through an explicit systemctl show argv."""

        requested_name = validate_systemd_unit_identity(
            unit_name, field_name="unit_name"
        )
        argv = (
            self._systemctl_path,
            "--system",
            "--no-pager",
            "--no-ask-password",
            "show",
            "--all",
            "--property=" + ",".join(_SYSTEMCTL_PROPERTIES),
            "--",
            requested_name,
        )
        try:
            result = self._runner(argv, timeout_seconds=self._timeout_seconds)
        except SystemdDependencyCommandError:
            raise
        except OSError as exc:
            raise SystemdDependencyCommandError(
                f"systemctl execution failed: {exc}"
            ) from exc

        stdout = _decode_capture(result.stdout, stream_name="stdout")
        stderr = _decode_capture(result.stderr, stream_name="stderr")
        if result.returncode != 0:
            detail = _bounded_text(stderr or stdout or "no diagnostic output")
            raise SystemdDependencyCommandError(
                f"systemctl show failed for {requested_name} "
                f"with exit status {result.returncode}: {detail}"
            )

        properties = _parse_properties(stdout)
        canonical_name = validate_systemd_unit_identity(
            properties["Id"],
            field_name="Id",
        )
        captured_at = self._clock()
        _validate_aware_datetime(captured_at, field_name="clock result")
        return SystemdDependencyUnitSnapshot(
            requested_name=requested_name,
            canonical_name=canonical_name,
            names=_parse_names(properties, canonical_name),
            load_state=_validate_nonempty_text(
                properties["LoadState"],
                field_name="LoadState",
            ),
            requires=_parse_unit_words(
                properties["Requires"], property_name="Requires"
            ),
            wants=_parse_unit_words(properties["Wants"], property_name="Wants"),
            after=_parse_unit_words(properties["After"], property_name="After"),
            before=_parse_unit_words(properties["Before"], property_name="Before"),
            captured_at=captured_at,
        )


class SystemdDependencyDiscovery:
    """Bounded breadth-first dependency discovery over requirement relations."""

    def __init__(
        self,
        *,
        reader: DependencyUnitReader,
        max_depth: int = 2,
        max_units: int = 128,
        boot_id_reader: Callable[[], str] = read_current_boot_id,
        event_id_factory: EventIdFactory | None = None,
    ) -> None:
        if not callable(getattr(reader, "read_unit", None)):
            raise TypeError("reader must provide a callable read_unit")
        _validate_discovery_bounds(max_depth, max_units)
        if not callable(boot_id_reader):
            raise TypeError("boot_id_reader must be callable")
        if event_id_factory is not None and not callable(event_id_factory):
            raise TypeError("event_id_factory must be callable or None")
        self._reader: DependencyUnitReader = reader
        self._max_depth = max_depth
        self._max_units = max_units
        self._boot_id_reader = boot_id_reader
        self._event_id_factory = event_id_factory

    def discover(self, root_unit: str) -> SystemdDependencyDiscoveryReport:
        """Discover a bounded requirement frontier while retaining ordering evidence."""

        root_requested = validate_systemd_unit_identity(
            root_unit,
            field_name="root_unit",
        )
        boot_id = _normalize_boot_id(self._boot_id_reader())
        queue: deque[tuple[str, int]] = deque([(root_requested, 0)])
        enqueued: set[str] = {root_requested}
        seen_canonical: set[str] = set()
        units: list[DiscoveredSystemdUnit] = []
        failures: list[SystemdDependencyDiscoveryFailure] = []
        unexpanded_requirement_targets: set[str] = set()

        while queue:
            requested_name, depth = queue.popleft()
            try:
                snapshot = self._reader.read_unit(requested_name)
            except SystemdDependencyReadError as exc:
                if depth == 0:
                    raise
                failures.append(
                    SystemdDependencyDiscoveryFailure(
                        requested_unit=requested_name,
                        depth=depth,
                        error_type=type(exc).__name__,
                        message=_bounded_text(str(exc) or type(exc).__name__),
                    )
                )
                continue

            if snapshot.canonical_name in seen_canonical:
                continue
            seen_canonical.add(snapshot.canonical_name)
            source_event = _snapshot_to_event(
                snapshot,
                boot_id=boot_id,
                event_id_factory=self._event_id_factory,
            )
            evidence = _project_snapshot_evidence(
                snapshot, source_event, boot_id=boot_id
            )
            units.append(
                DiscoveredSystemdUnit(
                    depth=depth,
                    snapshot=snapshot,
                    source_event=source_event,
                    evidence=evidence,
                )
            )

            traversal_targets = _requirement_targets(snapshot)
            if depth >= self._max_depth:
                unexpanded_requirement_targets.update(
                    target
                    for target in traversal_targets
                    if target not in enqueued and target not in seen_canonical
                )
                continue

            for target in traversal_targets:
                if target in enqueued or target in seen_canonical:
                    continue
                if len(enqueued) >= self._max_units:
                    raise SystemdDependencyDiscoveryCapacityError(
                        "dependency discovery frontier exceeds max_units; "
                        "no unit was silently evicted"
                    )
                enqueued.add(target)
                queue.append((target, depth + 1))

        root_canonical = units[0].snapshot.canonical_name
        return SystemdDependencyDiscoveryReport(
            root_requested_unit=root_requested,
            root_canonical_unit=root_canonical,
            boot_id=boot_id,
            max_depth=self._max_depth,
            max_units=self._max_units,
            units=tuple(units),
            failures=tuple(failures),
            truncated_by_depth=bool(unexpanded_requirement_targets),
            unexpanded_requirement_count=len(unexpanded_requirement_targets),
        )


def validate_systemd_unit_identity(value: str, *, field_name: str) -> str:
    """Validate a generic unit identity for safe argv use, not full systemd syntax."""

    if not isinstance(value, str):
        raise SystemdDependencyProtocolError(f"{field_name} must be a string")
    if not value or value != value.strip():
        raise SystemdDependencyProtocolError(
            f"{field_name} must be non-empty without surrounding whitespace"
        )
    if len(value) > _MAX_UNIT_NAME_LENGTH:
        raise SystemdDependencyProtocolError(f"{field_name} is too long")
    if not value.isascii():
        raise SystemdDependencyProtocolError(
            f"{field_name} must use systemd's ASCII/escaped unit representation"
        )
    if (
        "\x00" in value
        or "/" in value
        or any(character.isspace() for character in value)
    ):
        raise SystemdDependencyProtocolError(
            f"{field_name} contains an unsafe unit-name character"
        )
    if "." not in value or value.endswith("."):
        raise SystemdDependencyProtocolError(
            f"{field_name} must include a unit-type suffix"
        )
    return value


def _snapshot_to_event(
    snapshot: SystemdDependencyUnitSnapshot,
    *,
    boot_id: str,
    event_id_factory: EventIdFactory | None,
) -> SentinelEvent:
    attributes: dict[str, object] = {
        "observation_type": SYSTEMD_DEPENDENCY_OBSERVATION_TYPE,
        "requested_name": snapshot.requested_name,
        "canonical_name": snapshot.canonical_name,
        "names": list(snapshot.names),
        "load_state": snapshot.load_state,
        "boot_id": boot_id,
        "requires": list(snapshot.requires),
        "wants": list(snapshot.wants),
        "after": list(snapshot.after),
        "before": list(snapshot.before),
        "causal_claims_assigned": False,
    }
    if event_id_factory is None:
        return SentinelEvent(
            kind=EventKind.OBSERVATION,
            source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
            message=f"systemd dependency observation for {snapshot.canonical_name}",
            severity=EventSeverity.INFO,
            attributes=attributes,
            occurred_at=snapshot.captured_at,
        )
    event_id = event_id_factory()
    _validate_nonempty_text(event_id, field_name="event_id_factory result")
    return SentinelEvent(
        kind=EventKind.OBSERVATION,
        source=SYSTEMD_DEPENDENCY_OBSERVATION_SOURCE,
        message=f"systemd dependency observation for {snapshot.canonical_name}",
        severity=EventSeverity.INFO,
        attributes=attributes,
        event_id=event_id,
        occurred_at=snapshot.captured_at,
    )


def _project_snapshot_evidence(
    snapshot: SystemdDependencyUnitSnapshot,
    event: SentinelEvent,
    *,
    boot_id: str,
) -> tuple[DependencyEvidence, ...]:
    subject = DependencyEndpoint(
        kind=DependencyEntityKind.SYSTEMD_UNIT,
        identity=snapshot.canonical_name,
    )
    projected: list[DependencyEvidence] = []
    try:
        for relation, field_name in _RELATION_FIELDS:
            for target in getattr(snapshot, field_name):
                projected.append(
                    DependencyEvidence(
                        evidence_id=_evidence_id(
                            source_event_id=event.event_id,
                            canonical_unit=snapshot.canonical_name,
                            relation=relation,
                            target_unit=target,
                        ),
                        source_event_id=event.event_id,
                        observed_at=event.occurred_at,
                        boot_id=boot_id,
                        subject=subject,
                        relation=relation,
                        object=DependencyEndpoint(
                            kind=DependencyEntityKind.SYSTEMD_UNIT,
                            identity=target,
                        ),
                        origin=DependencyEvidenceOrigin.SYSTEMD_MANAGER_PROPERTY,
                        configuration_origin=(
                            DependencyConfigurationOrigin.MANAGER_MERGED_UNRESOLVED
                        ),
                        source_property=source_property_for_relation(relation),
                        causal_claim=False,
                    )
                )
    except DependencyModelError as exc:
        raise SystemdDependencyProtocolError(str(exc)) from exc
    return tuple(projected)


def _evidence_id(
    *,
    source_event_id: str,
    canonical_unit: str,
    relation: DependencyRelation,
    target_unit: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(_EVIDENCE_ID_DOMAIN)
    for value in (
        source_event_id,
        canonical_unit,
        relation.value,
        target_unit,
    ):
        digest.update(value.encode("utf-8"))
        digest.update(b"\x00")
    return f"depev-{digest.hexdigest()}"


def _requirement_targets(snapshot: SystemdDependencyUnitSnapshot) -> tuple[str, ...]:
    targets: list[str] = []
    seen: set[str] = set()
    for relation, field_name in _RELATION_FIELDS:
        if relation not in _TRAVERSAL_RELATIONS:
            continue
        for target in getattr(snapshot, field_name):
            if target in seen:
                continue
            seen.add(target)
            targets.append(target)
    return tuple(targets)


def _validate_discovery_bounds(max_depth: int, max_units: int) -> None:
    if (
        isinstance(max_depth, bool)
        or not isinstance(max_depth, int)
        or not 0 <= max_depth <= _MAX_DISCOVERY_DEPTH
    ):
        raise ValueError(f"max_depth must be between 0 and {_MAX_DISCOVERY_DEPTH}")
    if (
        isinstance(max_units, bool)
        or not isinstance(max_units, int)
        or not 1 <= max_units <= _MAX_DISCOVERY_UNITS
    ):
        raise ValueError(f"max_units must be between 1 and {_MAX_DISCOVERY_UNITS}")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _command_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LC_ALL": "C",
            "SYSTEMD_COLORS": "0",
            "SYSTEMD_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    return environment


def _default_runner(
    argv: Sequence[str],
    *,
    timeout_seconds: float,
) -> SystemctlCommandResult:
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
        raise SystemdDependencyCommandTimeoutError(
            f"systemctl read exceeded {timeout_seconds:.3f} seconds"
        ) from exc
    except OSError as exc:
        raise SystemdDependencyCommandError(
            f"systemctl execution failed: {exc}"
        ) from exc
    return SystemctlCommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _decode_capture(value: bytes, *, stream_name: str) -> str:
    if len(value) > _MAX_CAPTURE_BYTES:
        raise SystemdDependencyProtocolError(
            f"systemctl {stream_name} exceeded {_MAX_CAPTURE_BYTES} bytes"
        )
    if b"\x00" in value:
        raise SystemdDependencyProtocolError(
            f"systemctl {stream_name} contains a NUL byte"
        )
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SystemdDependencyProtocolError(
            f"systemctl {stream_name} is not valid UTF-8"
        ) from exc


def _parse_properties(stdout: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line_number, line in enumerate(stdout.splitlines(), start=1):
        if not line:
            continue
        if "=" not in line:
            raise SystemdDependencyProtocolError(
                f"systemctl output line {line_number} is not a property assignment"
            )
        key, value = line.split("=", 1)
        if not key:
            raise SystemdDependencyProtocolError(
                f"systemctl output line {line_number} has an empty property name"
            )
        if key in properties:
            raise SystemdDependencyProtocolError(f"duplicate systemctl property: {key}")
        if key not in _REQUIRED_PROPERTIES:
            raise SystemdDependencyProtocolError(
                f"unexpected systemctl property: {key}"
            )
        properties[key] = value
    missing = sorted(_REQUIRED_PROPERTIES - properties.keys())
    if missing:
        raise SystemdDependencyProtocolError(
            "systemctl output is missing required properties: " + ", ".join(missing)
        )
    return properties


def _parse_names(
    properties: Mapping[str, str],
    canonical_name: str,
) -> tuple[str, ...]:
    names = _parse_unit_words(properties["Names"], property_name="Names")
    if not names:
        return (canonical_name,)
    if canonical_name not in names:
        raise SystemdDependencyProtocolError("Names must include Id")
    return names


def _parse_unit_words(value: str, *, property_name: str) -> tuple[str, ...]:
    if not value:
        return ()
    words = tuple(value.split())
    if len(words) > _MAX_PROPERTY_ENTRIES:
        raise SystemdDependencyProtocolError(
            f"{property_name} exceeds the bounded entry limit"
        )
    if len(set(words)) != len(words):
        raise SystemdDependencyProtocolError(
            f"{property_name} contains duplicate unit names"
        )
    for index, word in enumerate(words):
        validate_systemd_unit_identity(
            word,
            field_name=f"{property_name}[{index}]",
        )
    return words


def _validate_unit_tuple(values: tuple[str, ...], *, field_name: str) -> None:
    if not isinstance(values, tuple):
        raise SystemdDependencyProtocolError(f"{field_name} must be a tuple")
    if len(values) > _MAX_PROPERTY_ENTRIES:
        raise SystemdDependencyProtocolError(
            f"{field_name} exceeds the bounded entry limit"
        )
    if len(set(values)) != len(values):
        raise SystemdDependencyProtocolError(f"{field_name} contains duplicates")
    for index, value in enumerate(values):
        validate_systemd_unit_identity(value, field_name=f"{field_name}[{index}]")


def _validate_nonempty_text(value: object, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
    ):
        raise SystemdDependencyProtocolError(
            f"{field_name} must be non-empty text without surrounding whitespace"
        )
    return value


def _validate_aware_datetime(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise SystemdDependencyProtocolError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise SystemdDependencyProtocolError(f"{field_name} must be timezone-aware")
    return value


def _normalize_boot_id(value: str) -> str:
    try:
        return normalize_boot_id(value, field_name="boot_id")
    except SystemBootIdError as exc:
        raise SystemdDependencyProtocolError(str(exc)) from exc


def _bounded_text(value: str) -> str:
    normalized = " ".join(value.split()) or "unknown systemd dependency read failure"
    if len(normalized) <= _MAX_ERROR_MESSAGE_CHARS:
        return normalized
    return normalized[: _MAX_ERROR_MESSAGE_CHARS - 3] + "..."
