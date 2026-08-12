"""Linux network-interface discovery and raw statistics for Sentinel-X."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Final


_PROC_NET_DEV_MAX_BYTES: Final[int] = 4_194_304
_PROC_NET_DEV_BASE_COUNTER_COUNT: Final[int] = 16

ARPHRD_LOOPBACK: Final[int] = 772


class NetworkObservationError(RuntimeError):
    """Base error for Linux network observability failures."""


class NetworkStatsReadError(NetworkObservationError):
    """Raised when /proc/net/dev cannot be read safely."""


class NetworkStatsParseError(NetworkObservationError):
    """Raised when /proc/net/dev contains malformed data."""


class NetworkIdentityReadError(NetworkObservationError):
    """Raised when required sysfs network identity cannot be read."""


class NetworkOperState(str, Enum):
    """RFC2863-style Linux network operational states."""

    UNKNOWN = "unknown"
    NOT_PRESENT = "notpresent"
    DOWN = "down"
    LOWER_LAYER_DOWN = "lowerlayerdown"
    TESTING = "testing"
    DORMANT = "dormant"
    UP = "up"


@dataclass(frozen=True, slots=True)
class NetworkInterfaceIdentity:
    """Linux network-interface identity and topology metadata."""

    name: str
    ifindex: int | None
    iflink: int | None
    protocol_type: int | None
    mtu: int | None
    operstate: NetworkOperState | None
    address: str | None
    carrier: bool | None
    sysfs_path: str | None

    def __post_init__(self) -> None:
        """Validate interface identity metadata."""

        _require_interface_name(self.name)

        for field_name, field_value in (
            ("ifindex", self.ifindex),
            ("iflink", self.iflink),
            ("protocol_type", self.protocol_type),
            ("mtu", self.mtu),
        ):
            if field_value is not None:
                _require_nonnegative_int(field_name, field_value)

        if self.ifindex == 0:
            raise ValueError("ifindex must be greater than zero")

        if self.iflink == 0:
            raise ValueError("iflink must be greater than zero")

        if self.operstate is not None and not isinstance(
            self.operstate,
            NetworkOperState,
        ):
            raise TypeError("operstate must be a NetworkOperState or None")

        if self.address is not None:
            _require_nonempty_text("address", self.address)

        if self.carrier is not None and not isinstance(self.carrier, bool):
            raise TypeError("carrier must be a bool or None")

        if self.sysfs_path is not None:
            _require_absolute_path("sysfs_path", self.sysfs_path)

    @property
    def enriched(self) -> bool:
        """Return whether core sysfs identity metadata was resolved."""

        return self.ifindex is not None

    @property
    def is_loopback(self) -> bool:
        """Return whether Linux reports the loopback ARP hardware type."""

        return self.protocol_type == ARPHRD_LOOPBACK

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly interface identity."""

        return {
            "name": self.name,
            "ifindex": self.ifindex,
            "iflink": self.iflink,
            "protocol_type": self.protocol_type,
            "mtu": self.mtu,
            "operstate": (None if self.operstate is None else self.operstate.value),
            "address": self.address,
            "carrier": self.carrier,
            "sysfs_path": self.sysfs_path,
            "enriched": self.enriched,
            "is_loopback": self.is_loopback,
        }


@dataclass(frozen=True, slots=True)
class NetworkInterfaceStats:
    """Raw /proc/net/dev counters for one network interface."""

    rx_bytes: int
    rx_packets: int
    rx_errors: int
    rx_dropped: int
    rx_fifo_errors: int
    rx_frame_errors_aggregate: int
    rx_compressed: int
    rx_multicast: int
    tx_bytes: int
    tx_packets: int
    tx_errors: int
    tx_dropped: int
    tx_fifo_errors: int
    tx_collisions: int
    tx_carrier_errors_aggregate: int
    tx_compressed: int
    extra_fields: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        """Validate raw interface counters."""

        counters = (
            ("rx_bytes", self.rx_bytes),
            ("rx_packets", self.rx_packets),
            ("rx_errors", self.rx_errors),
            ("rx_dropped", self.rx_dropped),
            ("rx_fifo_errors", self.rx_fifo_errors),
            (
                "rx_frame_errors_aggregate",
                self.rx_frame_errors_aggregate,
            ),
            ("rx_compressed", self.rx_compressed),
            ("rx_multicast", self.rx_multicast),
            ("tx_bytes", self.tx_bytes),
            ("tx_packets", self.tx_packets),
            ("tx_errors", self.tx_errors),
            ("tx_dropped", self.tx_dropped),
            ("tx_fifo_errors", self.tx_fifo_errors),
            ("tx_collisions", self.tx_collisions),
            (
                "tx_carrier_errors_aggregate",
                self.tx_carrier_errors_aggregate,
            ),
            ("tx_compressed", self.tx_compressed),
        )

        for counter_name, counter_value in counters:
            _require_nonnegative_int(counter_name, counter_value)

        for extra_value in self.extra_fields:
            _require_nonnegative_int(
                "extra network statistics field",
                extra_value,
            )

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly raw network statistics."""

        return {
            "rx_bytes": self.rx_bytes,
            "rx_packets": self.rx_packets,
            "rx_errors": self.rx_errors,
            "rx_dropped": self.rx_dropped,
            "rx_fifo_errors": self.rx_fifo_errors,
            "rx_frame_errors_aggregate": self.rx_frame_errors_aggregate,
            "rx_compressed": self.rx_compressed,
            "rx_multicast": self.rx_multicast,
            "tx_bytes": self.tx_bytes,
            "tx_packets": self.tx_packets,
            "tx_errors": self.tx_errors,
            "tx_dropped": self.tx_dropped,
            "tx_fifo_errors": self.tx_fifo_errors,
            "tx_collisions": self.tx_collisions,
            "tx_carrier_errors_aggregate": (self.tx_carrier_errors_aggregate),
            "tx_compressed": self.tx_compressed,
            "extra_fields": list(self.extra_fields),
        }


@dataclass(frozen=True, slots=True)
class NetworkInterfaceRecord:
    """Raw statistics plus identity for one network interface."""

    identity: NetworkInterfaceIdentity
    stats: NetworkInterfaceStats

    def __post_init__(self) -> None:
        """Validate record component types."""

        if not isinstance(self.identity, NetworkInterfaceIdentity):
            raise TypeError("identity must be a NetworkInterfaceIdentity")

        if not isinstance(self.stats, NetworkInterfaceStats):
            raise TypeError("stats must be a NetworkInterfaceStats")

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly interface evidence."""

        return {
            "identity": self.identity.to_dict(),
            "stats": self.stats.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class NetworkIdentityProbeFailure:
    """One isolated sysfs identity-enrichment failure."""

    interface_name: str
    error_type: str
    error_message: str

    def __post_init__(self) -> None:
        """Validate identity failure metadata."""

        _require_interface_name(self.interface_name)
        _require_nonempty_text("error_type", self.error_type)
        _require_nonempty_text("error_message", self.error_message)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly failure evidence."""

        return {
            "interface_name": self.interface_name,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class NetworkStatsSnapshot:
    """One timestamped network-interface statistics snapshot."""

    captured_at: datetime
    interfaces: tuple[NetworkInterfaceRecord, ...]
    identity_failures: tuple[NetworkIdentityProbeFailure, ...]

    def __post_init__(self) -> None:
        """Validate snapshot structure and interface uniqueness."""

        _require_aware_datetime("captured_at", self.captured_at)

        if not self.interfaces:
            raise ValueError("interfaces must not be empty")

        interface_names: list[str] = []
        resolved_ifindexes: list[int] = []

        for interface in self.interfaces:
            if not isinstance(interface, NetworkInterfaceRecord):
                raise TypeError(
                    "interfaces must contain NetworkInterfaceRecord objects"
                )

            interface_names.append(interface.identity.name)

            if interface.identity.ifindex is not None:
                resolved_ifindexes.append(interface.identity.ifindex)

        if len(set(interface_names)) != len(interface_names):
            raise ValueError("network snapshot contains duplicate interface names")

        if len(set(resolved_ifindexes)) != len(resolved_ifindexes):
            raise ValueError("network snapshot contains duplicate resolved ifindexes")

        for failure in self.identity_failures:
            if not isinstance(failure, NetworkIdentityProbeFailure):
                raise TypeError(
                    "identity_failures must contain NetworkIdentityProbeFailure objects"
                )

    @property
    def interface_count(self) -> int:
        """Return the number of observed network interfaces."""

        return len(self.interfaces)

    @property
    def enriched_count(self) -> int:
        """Return interfaces with successfully resolved sysfs identity."""

        return sum(interface.identity.enriched for interface in self.interfaces)

    @property
    def unenriched_count(self) -> int:
        """Return interfaces whose sysfs identity could not be resolved."""

        return self.interface_count - self.enriched_count

    @property
    def loopback_count(self) -> int:
        """Return interfaces identified as Linux loopback devices."""

        return sum(interface.identity.is_loopback for interface in self.interfaces)

    def to_dict(self) -> dict[str, object]:
        """Return serialization-friendly network snapshot evidence."""

        return {
            "captured_at": self.captured_at.isoformat(),
            "summary": {
                "interface_count": self.interface_count,
                "enriched_count": self.enriched_count,
                "unenriched_count": self.unenriched_count,
                "loopback_count": self.loopback_count,
                "identity_failure_count": len(self.identity_failures),
            },
            "interfaces": [interface.to_dict() for interface in self.interfaces],
            "identity_failures": [
                failure.to_dict() for failure in self.identity_failures
            ],
        }


@dataclass(frozen=True, slots=True)
class _ParsedNetworkInterface:
    """Internal /proc/net/dev record before sysfs enrichment."""

    name: str
    stats: NetworkInterfaceStats


class LinuxNetworkReader:
    """Read raw Linux interface statistics and enrich them from sysfs."""

    def __init__(
        self,
        *,
        proc_net_dev_path: str | Path = "/proc/net/dev",
        sys_class_net_root: str | Path = "/sys/class/net",
    ) -> None:
        self._proc_net_dev_path = Path(proc_net_dev_path)
        self._sys_class_net_root = Path(sys_class_net_root)

    def read_snapshot(self) -> NetworkStatsSnapshot:
        """Read one network statistics snapshot."""

        parsed_interfaces = _parse_proc_net_dev(
            _read_bounded_text(
                self._proc_net_dev_path,
                max_bytes=_PROC_NET_DEV_MAX_BYTES,
            )
        )

        records: list[NetworkInterfaceRecord] = []
        failures: list[NetworkIdentityProbeFailure] = []

        for parsed in parsed_interfaces:
            identity, failure = _probe_interface_identity(
                parsed.name,
                sys_class_net_root=self._sys_class_net_root,
            )

            records.append(
                NetworkInterfaceRecord(
                    identity=identity,
                    stats=parsed.stats,
                )
            )

            if failure is not None:
                failures.append(failure)

        try:
            return NetworkStatsSnapshot(
                captured_at=datetime.now(timezone.utc),
                interfaces=tuple(records),
                identity_failures=tuple(failures),
            )

        except (TypeError, ValueError) as exc:
            raise NetworkStatsParseError(
                f"invalid network statistics snapshot: {exc}"
            ) from exc


def _read_bounded_text(path: Path, *, max_bytes: int) -> str:
    """Read one Linux network metadata file with a strict size limit."""

    try:
        with path.open("rb") as file_handle:
            data = file_handle.read(max_bytes + 1)

    except (OSError, ValueError) as exc:
        raise NetworkStatsReadError(f"could not read {path}: {exc}") from exc

    if len(data) > max_bytes:
        raise NetworkStatsReadError(
            f"network statistics exceed {max_bytes} bytes: {path}"
        )

    try:
        return data.decode("utf-8")

    except UnicodeDecodeError as exc:
        raise NetworkStatsReadError(
            f"network statistics are not valid UTF-8: {path}"
        ) from exc


def _parse_proc_net_dev(
    text: str,
) -> tuple[_ParsedNetworkInterface, ...]:
    """Parse the historical Linux /proc/net/dev text interface."""

    lines = text.splitlines()

    if len(lines) < 3:
        raise NetworkStatsParseError(
            "/proc/net/dev does not contain headers and interface records"
        )

    header_one = lines[0]
    header_two = lines[1]

    if (
        "Inter-|" not in header_one
        or "Receive" not in header_one
        or "Transmit" not in header_one
    ):
        raise NetworkStatsParseError("/proc/net/dev first header line is invalid")

    if "face" not in header_two or "bytes" not in header_two:
        raise NetworkStatsParseError("/proc/net/dev second header line is invalid")

    records: list[_ParsedNetworkInterface] = []
    seen_names: set[str] = set()

    for line_number, raw_line in enumerate(
        lines[2:],
        start=3,
    ):
        if not raw_line.strip():
            continue

        name_part, separator, counters_part = raw_line.rpartition(":")

        if not separator:
            raise NetworkStatsParseError(
                f"/proc/net/dev line {line_number} is missing ':'"
            )

        name = name_part.strip()

        try:
            _require_interface_name(name)

        except (TypeError, ValueError) as exc:
            raise NetworkStatsParseError(
                f"invalid interface name on /proc/net/dev line {line_number}: {exc}"
            ) from exc

        if name in seen_names:
            raise NetworkStatsParseError(
                f"duplicate interface name {name!r} on /proc/net/dev line {line_number}"
            )

        raw_values = counters_part.split()

        if len(raw_values) < _PROC_NET_DEV_BASE_COUNTER_COUNT:
            raise NetworkStatsParseError(
                f"/proc/net/dev line {line_number} has fewer than "
                f"{_PROC_NET_DEV_BASE_COUNTER_COUNT} statistics counters"
            )

        values = tuple(
            _parse_nonnegative_int(
                raw_value,
                field_name="network statistics counter",
                line_number=line_number,
            )
            for raw_value in raw_values
        )

        stats = NetworkInterfaceStats(
            rx_bytes=values[0],
            rx_packets=values[1],
            rx_errors=values[2],
            rx_dropped=values[3],
            rx_fifo_errors=values[4],
            rx_frame_errors_aggregate=values[5],
            rx_compressed=values[6],
            rx_multicast=values[7],
            tx_bytes=values[8],
            tx_packets=values[9],
            tx_errors=values[10],
            tx_dropped=values[11],
            tx_fifo_errors=values[12],
            tx_collisions=values[13],
            tx_carrier_errors_aggregate=values[14],
            tx_compressed=values[15],
            extra_fields=values[16:],
        )

        records.append(
            _ParsedNetworkInterface(
                name=name,
                stats=stats,
            )
        )

        seen_names.add(name)

    if not records:
        raise NetworkStatsParseError(
            "/proc/net/dev does not contain any interface records"
        )

    return tuple(records)


def _probe_interface_identity(
    interface_name: str,
    *,
    sys_class_net_root: Path,
) -> tuple[
    NetworkInterfaceIdentity,
    NetworkIdentityProbeFailure | None,
]:
    """Resolve stable network identity while isolating sysfs races."""

    sysfs_path = sys_class_net_root / interface_name

    try:
        if not sysfs_path.is_dir():
            raise NetworkIdentityReadError(
                f"sysfs interface path is unavailable: {sysfs_path}"
            )

        ifindex = _read_sysfs_positive_int(
            sysfs_path / "ifindex",
            field_name="ifindex",
        )
        iflink = _read_sysfs_positive_int(
            sysfs_path / "iflink",
            field_name="iflink",
        )
        protocol_type = _read_sysfs_nonnegative_int(
            sysfs_path / "type",
            field_name="type",
        )
        mtu = _read_sysfs_nonnegative_int(
            sysfs_path / "mtu",
            field_name="mtu",
        )
        operstate = _read_operstate(
            sysfs_path / "operstate",
        )
        address = _read_required_sysfs_text(
            sysfs_path / "address",
            field_name="address",
        )
        carrier = _read_optional_carrier(
            sysfs_path / "carrier",
        )

        identity = NetworkInterfaceIdentity(
            name=interface_name,
            ifindex=ifindex,
            iflink=iflink,
            protocol_type=protocol_type,
            mtu=mtu,
            operstate=operstate,
            address=address,
            carrier=carrier,
            sysfs_path=str(sysfs_path),
        )

        return identity, None

    except NetworkIdentityReadError as exc:
        identity = NetworkInterfaceIdentity(
            name=interface_name,
            ifindex=None,
            iflink=None,
            protocol_type=None,
            mtu=None,
            operstate=None,
            address=None,
            carrier=None,
            sysfs_path=None,
        )

        failure = NetworkIdentityProbeFailure(
            interface_name=interface_name,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )

        return identity, failure


def _read_required_sysfs_text(
    path: Path,
    *,
    field_name: str,
) -> str:
    """Read one required single-line sysfs attribute."""

    try:
        value = path.read_text(encoding="utf-8").strip()

    except (OSError, UnicodeError) as exc:
        raise NetworkIdentityReadError(
            f"could not read {field_name} from {path}: {exc}"
        ) from exc

    if not value:
        raise NetworkIdentityReadError(f"{field_name} is empty in {path}")

    return value


def _read_sysfs_positive_int(
    path: Path,
    *,
    field_name: str,
) -> int:
    """Read one strictly positive decimal sysfs integer."""

    value = _read_sysfs_nonnegative_int(
        path,
        field_name=field_name,
    )

    if value == 0:
        raise NetworkIdentityReadError(
            f"{field_name} must be greater than zero in {path}"
        )

    return value


def _read_sysfs_nonnegative_int(
    path: Path,
    *,
    field_name: str,
) -> int:
    """Read one non-negative decimal sysfs integer."""

    raw_value = _read_required_sysfs_text(
        path,
        field_name=field_name,
    )

    try:
        value = int(raw_value, 10)

    except ValueError as exc:
        raise NetworkIdentityReadError(
            f"invalid {field_name} in {path}: {raw_value!r}"
        ) from exc

    if value < 0:
        raise NetworkIdentityReadError(
            f"negative {field_name} in {path}: {raw_value!r}"
        )

    return value


def _read_operstate(path: Path) -> NetworkOperState:
    """Read and validate the Linux network operational state."""

    raw_value = _read_required_sysfs_text(
        path,
        field_name="operstate",
    )

    try:
        return NetworkOperState(raw_value)

    except ValueError as exc:
        raise NetworkIdentityReadError(
            f"invalid operstate in {path}: {raw_value!r}"
        ) from exc


def _read_optional_carrier(path: Path) -> bool | None:
    """Read the optional Linux physical-link carrier state."""

    try:
        raw_value = path.read_text(encoding="utf-8").strip()

    except (OSError, UnicodeError):
        return None

    if raw_value == "0":
        return False

    if raw_value == "1":
        return True

    return None


def _parse_nonnegative_int(
    raw_value: str,
    *,
    field_name: str,
    line_number: int,
) -> int:
    """Parse one non-negative /proc/net/dev integer."""

    try:
        value = int(raw_value)

    except ValueError as exc:
        raise NetworkStatsParseError(
            f"invalid {field_name} on /proc/net/dev line {line_number}: {raw_value!r}"
        ) from exc

    if value < 0:
        raise NetworkStatsParseError(
            f"negative {field_name} on /proc/net/dev line {line_number}: {raw_value!r}"
        )

    return value


def _require_interface_name(value: str) -> None:
    """Require a non-empty Linux network-interface name."""

    if not isinstance(value, str):
        raise TypeError("interface name must be a string")

    if not value:
        raise ValueError("interface name must not be empty")

    if "\x00" in value:
        raise ValueError("interface name must not contain NUL")

    if "/" in value:
        raise ValueError("interface name must not contain '/'")


def _require_nonempty_text(name: str, value: str) -> None:
    """Require a non-empty string."""

    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")

    if not value:
        raise ValueError(f"{name} must not be empty")


def _require_absolute_path(name: str, value: str) -> None:
    """Require a non-empty absolute path."""

    _require_nonempty_text(name, value)

    if not value.startswith("/"):
        raise ValueError(f"{name} must be an absolute path")


def _require_nonnegative_int(name: str, value: int) -> None:
    """Require a non-negative integer."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")

    if value < 0:
        raise ValueError(f"{name} must not be negative")


def _require_aware_datetime(name: str, value: datetime) -> None:
    """Require a timezone-aware datetime."""

    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
