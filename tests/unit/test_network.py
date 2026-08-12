"""Unit tests for Sentinel-X Linux network observability."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sentinel_x.observability import (
    ARPHRD_LOOPBACK,
    LinuxNetworkReader,
    NetworkOperState,
    NetworkStatsParseError,
    NetworkStatsReadError,
)


_HEADER = (
    "Inter-|   Receive                                                "
    "|  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast"
    "|bytes    packets errs drop fifo colls carrier compressed\n"
)


def _network_line(
    *,
    name: str = "eth0",
    values: tuple[object, ...],
) -> str:
    counters = " ".join(str(value) for value in values)

    return f"  {name}: {counters}\n"


def _base_values() -> tuple[int, ...]:
    return (
        1000,
        10,
        1,
        2,
        3,
        4,
        5,
        6,
        2000,
        20,
        7,
        8,
        9,
        10,
        11,
        12,
    )


def _reader(
    root: Path,
    *,
    text: str | None,
) -> LinuxNetworkReader:
    proc_root = root / "proc" / "net"
    sys_root = root / "sys" / "class" / "net"

    proc_root.mkdir(parents=True)
    sys_root.mkdir(parents=True)

    proc_net_dev_path = proc_root / "dev"

    if text is not None:
        proc_net_dev_path.write_text(
            text,
            encoding="utf-8",
        )

    return LinuxNetworkReader(
        proc_net_dev_path=proc_net_dev_path,
        sys_class_net_root=sys_root,
    )


def _write_identity(
    root: Path,
    *,
    name: str,
    ifindex: int,
    iflink: int,
    protocol_type: int,
    mtu: int = 1500,
    operstate: str = "up",
    address: str = "00:11:22:33:44:55",
    carrier: str | None = "1",
) -> None:
    interface_root = root / "sys" / "class" / "net" / name
    interface_root.mkdir(parents=True)

    values = {
        "ifindex": f"{ifindex}\n",
        "iflink": f"{iflink}\n",
        "type": f"{protocol_type}\n",
        "mtu": f"{mtu}\n",
        "operstate": f"{operstate}\n",
        "address": f"{address}\n",
    }

    for filename, value in values.items():
        (interface_root / filename).write_text(
            value,
            encoding="utf-8",
        )

    if carrier is not None:
        (interface_root / "carrier").write_text(
            f"{carrier}\n",
            encoding="utf-8",
        )


class LinuxNetworkReaderTests(unittest.TestCase):
    """Tests for /proc/net/dev parsing and sysfs identity enrichment."""

    def test_parses_statistics_and_enriches_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            reader = _reader(
                root,
                text=_HEADER
                + _network_line(
                    values=_base_values(),
                ),
            )

            _write_identity(
                root,
                name="eth0",
                ifindex=2,
                iflink=2,
                protocol_type=1,
            )

            snapshot = reader.read_snapshot()

        self.assertEqual(snapshot.interface_count, 1)
        self.assertEqual(snapshot.enriched_count, 1)
        self.assertEqual(snapshot.unenriched_count, 0)
        self.assertEqual(len(snapshot.identity_failures), 0)

        interface = snapshot.interfaces[0]

        self.assertEqual(interface.identity.name, "eth0")
        self.assertEqual(interface.identity.ifindex, 2)
        self.assertEqual(interface.identity.iflink, 2)
        self.assertEqual(interface.identity.protocol_type, 1)
        self.assertEqual(interface.identity.mtu, 1500)
        self.assertIs(
            interface.identity.operstate,
            NetworkOperState.UP,
        )
        self.assertTrue(interface.identity.carrier)

        self.assertEqual(interface.stats.rx_bytes, 1000)
        self.assertEqual(interface.stats.rx_packets, 10)
        self.assertEqual(interface.stats.tx_bytes, 2000)
        self.assertEqual(interface.stats.tx_packets, 20)

    def test_loopback_is_identified_from_protocol_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            reader = _reader(
                root,
                text=_HEADER
                + _network_line(
                    name="lo",
                    values=_base_values(),
                ),
            )

            _write_identity(
                root,
                name="lo",
                ifindex=1,
                iflink=1,
                protocol_type=ARPHRD_LOOPBACK,
                mtu=65_536,
                operstate="unknown",
                address="00:00:00:00:00:00",
                carrier="1",
            )

            snapshot = reader.read_snapshot()

        identity = snapshot.interfaces[0].identity

        self.assertTrue(identity.is_loopback)
        self.assertEqual(snapshot.loopback_count, 1)

    def test_missing_sysfs_preserves_raw_statistics_and_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            reader = _reader(
                root,
                text=_HEADER
                + _network_line(
                    name="veth0",
                    values=_base_values(),
                ),
            )

            snapshot = reader.read_snapshot()

        self.assertEqual(snapshot.interface_count, 1)
        self.assertEqual(snapshot.enriched_count, 0)
        self.assertEqual(snapshot.unenriched_count, 1)
        self.assertEqual(len(snapshot.identity_failures), 1)

        interface = snapshot.interfaces[0]

        self.assertEqual(interface.identity.name, "veth0")
        self.assertIsNone(interface.identity.ifindex)
        self.assertEqual(interface.stats.rx_bytes, 1000)

        failure = snapshot.identity_failures[0]

        self.assertEqual(failure.interface_name, "veth0")
        self.assertEqual(
            failure.error_type,
            "NetworkIdentityReadError",
        )

    def test_invalid_counter_is_rejected(self) -> None:
        for invalid_value in ("bad", -1):
            with self.subTest(invalid_value=invalid_value):
                values = list(_base_values())
                values[0] = invalid_value

                with tempfile.TemporaryDirectory() as tmpdir:
                    reader = _reader(
                        Path(tmpdir),
                        text=_HEADER
                        + _network_line(
                            values=tuple(values),
                        ),
                    )

                    with self.assertRaises(NetworkStatsParseError):
                        reader.read_snapshot()

    def test_incomplete_statistics_record_is_rejected(self) -> None:
        values = _base_values()[:-1]

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_HEADER
                + _network_line(
                    values=values,
                ),
            )

            with self.assertRaises(NetworkStatsParseError):
                reader.read_snapshot()

    def test_duplicate_interface_name_is_rejected(self) -> None:
        line = _network_line(
            name="eth0",
            values=_base_values(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_HEADER + line + line,
            )

            with self.assertRaises(NetworkStatsParseError):
                reader.read_snapshot()

    def test_future_trailing_counters_are_preserved(self) -> None:
        values = (
            *_base_values(),
            123,
            456,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=_HEADER
                + _network_line(
                    values=values,
                ),
            )

            stats = reader.read_snapshot().interfaces[0].stats

        self.assertEqual(
            stats.extra_fields,
            (123, 456),
        )

    def test_invalid_operstate_isolated_as_identity_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            reader = _reader(
                root,
                text=_HEADER
                + _network_line(
                    values=_base_values(),
                ),
            )

            _write_identity(
                root,
                name="eth0",
                ifindex=2,
                iflink=2,
                protocol_type=1,
                operstate="impossible",
            )

            snapshot = reader.read_snapshot()

        self.assertEqual(snapshot.interface_count, 1)
        self.assertEqual(snapshot.enriched_count, 0)
        self.assertEqual(len(snapshot.identity_failures), 1)
        self.assertEqual(snapshot.interfaces[0].stats.rx_bytes, 1000)

    def test_missing_optional_carrier_does_not_fail_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            reader = _reader(
                root,
                text=_HEADER
                + _network_line(
                    values=_base_values(),
                ),
            )

            _write_identity(
                root,
                name="eth0",
                ifindex=2,
                iflink=2,
                protocol_type=1,
                carrier=None,
            )

            snapshot = reader.read_snapshot()

        identity = snapshot.interfaces[0].identity

        self.assertTrue(identity.enriched)
        self.assertIsNone(identity.carrier)
        self.assertEqual(len(snapshot.identity_failures), 0)

    def test_duplicate_resolved_ifindex_is_rejected(self) -> None:
        text = (
            _HEADER
            + _network_line(
                name="eth0",
                values=_base_values(),
            )
            + _network_line(
                name="eth1",
                values=_base_values(),
            )
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            reader = _reader(
                root,
                text=text,
            )

            _write_identity(
                root,
                name="eth0",
                ifindex=2,
                iflink=2,
                protocol_type=1,
            )
            _write_identity(
                root,
                name="eth1",
                ifindex=2,
                iflink=2,
                protocol_type=1,
            )

            with self.assertRaises(NetworkStatsParseError):
                reader.read_snapshot()

    def test_missing_proc_net_dev_is_reported_as_read_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            reader = _reader(
                Path(tmpdir),
                text=None,
            )

            with self.assertRaises(NetworkStatsReadError):
                reader.read_snapshot()

    def test_snapshot_serialization_preserves_raw_and_identity_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            reader = _reader(
                root,
                text=_HEADER
                + _network_line(
                    values=_base_values(),
                ),
            )

            _write_identity(
                root,
                name="eth0",
                ifindex=2,
                iflink=2,
                protocol_type=1,
            )

            payload = reader.read_snapshot().to_dict()

        self.assertEqual(
            payload["summary"],
            {
                "interface_count": 1,
                "enriched_count": 1,
                "unenriched_count": 0,
                "loopback_count": 0,
                "identity_failure_count": 0,
            },
        )

        interfaces = payload["interfaces"]

        self.assertIsInstance(interfaces, list)

        assert isinstance(interfaces, list)

        self.assertEqual(
            interfaces[0]["identity"]["ifindex"],
            2,
        )
        self.assertEqual(
            interfaces[0]["stats"]["rx_bytes"],
            1000,
        )


if __name__ == "__main__":
    unittest.main()
