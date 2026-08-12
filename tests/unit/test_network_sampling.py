"""Unit tests for Sentinel-X sampled network-interface metrics."""

from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

from sentinel_x.observability import (
    ARPHRD_LOOPBACK,
    NetworkInterfaceIdentity,
    NetworkInterfaceRecord,
    NetworkInterfaceStats,
    NetworkMatchMethod,
    NetworkObservation,
    NetworkOperState,
    NetworkSampleStatus,
    NetworkSamplingError,
    NetworkStatsSnapshot,
    build_network_observation,
)


_BASE_TIME = datetime(
    2026,
    1,
    1,
    tzinfo=timezone.utc,
)


def _identity(
    *,
    name: str = "eth0",
    ifindex: int | None = 2,
    iflink: int | None = 2,
    protocol_type: int | None = 1,
    mtu: int | None = 1500,
    operstate: NetworkOperState | None = NetworkOperState.UP,
    address: str | None = "00:11:22:33:44:55",
    carrier: bool | None = True,
    sysfs_path: str | None = "/sys/class/net/eth0",
) -> NetworkInterfaceIdentity:
    return NetworkInterfaceIdentity(
        name=name,
        ifindex=ifindex,
        iflink=iflink,
        protocol_type=protocol_type,
        mtu=mtu,
        operstate=operstate,
        address=address,
        carrier=carrier,
        sysfs_path=sysfs_path,
    )


def _stats(
    *,
    rx_bytes: int = 1000,
    rx_packets: int = 10,
    rx_errors: int = 1,
    rx_dropped: int = 2,
    rx_fifo_errors: int = 3,
    rx_frame_errors_aggregate: int = 4,
    rx_compressed: int = 5,
    rx_multicast: int = 6,
    tx_bytes: int = 2000,
    tx_packets: int = 20,
    tx_errors: int = 7,
    tx_dropped: int = 8,
    tx_fifo_errors: int = 9,
    tx_collisions: int = 10,
    tx_carrier_errors_aggregate: int = 11,
    tx_compressed: int = 12,
    extra_fields: tuple[int, ...] = (),
) -> NetworkInterfaceStats:
    return NetworkInterfaceStats(
        rx_bytes=rx_bytes,
        rx_packets=rx_packets,
        rx_errors=rx_errors,
        rx_dropped=rx_dropped,
        rx_fifo_errors=rx_fifo_errors,
        rx_frame_errors_aggregate=rx_frame_errors_aggregate,
        rx_compressed=rx_compressed,
        rx_multicast=rx_multicast,
        tx_bytes=tx_bytes,
        tx_packets=tx_packets,
        tx_errors=tx_errors,
        tx_dropped=tx_dropped,
        tx_fifo_errors=tx_fifo_errors,
        tx_collisions=tx_collisions,
        tx_carrier_errors_aggregate=tx_carrier_errors_aggregate,
        tx_compressed=tx_compressed,
        extra_fields=extra_fields,
    )


def _active_end_stats() -> NetworkInterfaceStats:
    return _stats(
        rx_bytes=3000,
        rx_packets=30,
        rx_errors=3,
        rx_dropped=8,
        rx_fifo_errors=7,
        rx_frame_errors_aggregate=10,
        rx_compressed=7,
        rx_multicast=16,
        tx_bytes=5000,
        tx_packets=50,
        tx_errors=11,
        tx_dropped=16,
        tx_fifo_errors=13,
        tx_collisions=14,
        tx_carrier_errors_aggregate=17,
        tx_compressed=18,
    )


def _record(
    *,
    identity: NetworkInterfaceIdentity | None = None,
    stats: NetworkInterfaceStats | None = None,
) -> NetworkInterfaceRecord:
    return NetworkInterfaceRecord(
        identity=identity or _identity(),
        stats=stats or _stats(),
    )


def _snapshot(
    interfaces: tuple[NetworkInterfaceRecord, ...],
    *,
    offset_seconds: float,
) -> NetworkStatsSnapshot:
    return NetworkStatsSnapshot(
        captured_at=_BASE_TIME + timedelta(seconds=offset_seconds),
        interfaces=interfaces,
        identity_failures=(),
    )


class NetworkSamplingTests(unittest.TestCase):
    """Tests for safe network-interface sampling and identity handling."""

    def test_calculates_network_rates_and_error_deltas(self) -> None:
        previous = _snapshot(
            (_record(),),
            offset_seconds=0.0,
        )
        current = _snapshot(
            (
                _record(
                    stats=_active_end_stats(),
                ),
            ),
            offset_seconds=2.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=2.0,
        )

        sample = observation.interfaces[0]
        metrics = sample.metrics

        self.assertIs(sample.status, NetworkSampleStatus.SAMPLED)
        self.assertIs(sample.match_method, NetworkMatchMethod.IFINDEX)
        self.assertIsNotNone(metrics)

        assert metrics is not None

        self.assertEqual(metrics.rx_bytes_delta, 2000)
        self.assertEqual(metrics.tx_bytes_delta, 3000)
        self.assertEqual(metrics.rx_packets_delta, 20)
        self.assertEqual(metrics.tx_packets_delta, 30)
        self.assertEqual(metrics.rx_errors_delta, 2)
        self.assertEqual(metrics.tx_errors_delta, 4)
        self.assertEqual(metrics.rx_dropped_delta, 6)
        self.assertEqual(metrics.tx_dropped_delta, 8)
        self.assertAlmostEqual(metrics.rx_bytes_per_second, 1000.0)
        self.assertAlmostEqual(metrics.tx_bytes_per_second, 1500.0)
        self.assertAlmostEqual(metrics.rx_packets_per_second, 10.0)
        self.assertAlmostEqual(metrics.tx_packets_per_second, 15.0)
        self.assertAlmostEqual(metrics.rx_errors_per_second, 1.0)
        self.assertAlmostEqual(metrics.tx_errors_per_second, 2.0)
        self.assertAlmostEqual(metrics.rx_dropped_per_second, 3.0)
        self.assertAlmostEqual(metrics.tx_dropped_per_second, 4.0)

    def test_same_ifindex_rename_remains_sampled(self) -> None:
        previous_identity = _identity(
            name="eth0",
            sysfs_path="/sys/class/net/eth0",
        )
        current_identity = _identity(
            name="lan0",
            sysfs_path="/sys/class/net/lan0",
        )

        previous = _snapshot(
            (
                _record(
                    identity=previous_identity,
                ),
            ),
            offset_seconds=0.0,
        )
        current = _snapshot(
            (
                _record(
                    identity=current_identity,
                    stats=_active_end_stats(),
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.interfaces[0]

        self.assertIs(sample.status, NetworkSampleStatus.SAMPLED)
        self.assertIs(sample.match_method, NetworkMatchMethod.IFINDEX)
        self.assertTrue(sample.name_changed)
        self.assertEqual(observation.renamed_count, 1)
        self.assertIsNotNone(sample.metrics)

    def test_same_name_with_new_ifindex_marks_identity_change(self) -> None:
        previous_identity = _identity(
            ifindex=2,
            iflink=2,
        )
        current_identity = _identity(
            ifindex=8,
            iflink=8,
        )

        previous = _snapshot(
            (_record(identity=previous_identity),),
            offset_seconds=0.0,
        )
        current = _snapshot(
            (
                _record(
                    identity=current_identity,
                    stats=_active_end_stats(),
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.interfaces[0]

        self.assertIs(sample.status, NetworkSampleStatus.IDENTITY_CHANGED)
        self.assertIs(sample.match_method, NetworkMatchMethod.NAME)
        self.assertIsNone(sample.metrics)
        self.assertEqual(observation.identity_changed_count, 1)

    def test_missing_ifindex_uses_unverified_identity_without_metrics(self) -> None:
        current_identity = _identity(
            ifindex=None,
            iflink=None,
            protocol_type=None,
            mtu=None,
            operstate=None,
            address=None,
            carrier=None,
            sysfs_path=None,
        )

        previous = _snapshot(
            (_record(),),
            offset_seconds=0.0,
        )
        current = _snapshot(
            (
                _record(
                    identity=current_identity,
                    stats=_active_end_stats(),
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.interfaces[0]

        self.assertIs(
            sample.status,
            NetworkSampleStatus.IDENTITY_UNVERIFIED,
        )
        self.assertIs(sample.match_method, NetworkMatchMethod.NAME)
        self.assertIsNone(sample.metrics)
        self.assertEqual(observation.identity_unverified_count, 1)

    def test_appeared_and_disappeared_interfaces_are_preserved(self) -> None:
        stable_identity = _identity()

        disappearing_identity = _identity(
            name="eth1",
            ifindex=3,
            iflink=3,
            address="00:11:22:33:44:66",
            sysfs_path="/sys/class/net/eth1",
        )

        appearing_identity = _identity(
            name="wg0",
            ifindex=7,
            iflink=7,
            address="00:11:22:33:44:77",
            sysfs_path="/sys/class/net/wg0",
        )

        previous = _snapshot(
            (
                _record(identity=stable_identity),
                _record(identity=disappearing_identity),
            ),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _record(
                    identity=stable_identity,
                    stats=_active_end_stats(),
                ),
                _record(identity=appearing_identity),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        statuses = {
            sample.identity.name: sample.status for sample in observation.interfaces
        }

        self.assertIs(statuses["eth0"], NetworkSampleStatus.SAMPLED)
        self.assertIs(statuses["wg0"], NetworkSampleStatus.APPEARED)
        self.assertIs(statuses["eth1"], NetworkSampleStatus.DISAPPEARED)
        self.assertEqual(observation.sampled_count, 1)
        self.assertEqual(observation.appeared_count, 1)
        self.assertEqual(observation.disappeared_count, 1)

    def test_counter_regression_marks_reset(self) -> None:
        previous = _snapshot(
            (_record(),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _record(
                    stats=_stats(
                        rx_bytes=999,
                    ),
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.interfaces[0]

        self.assertIs(sample.status, NetworkSampleStatus.COUNTER_RESET)
        self.assertEqual(sample.regressed_fields, ("rx_bytes",))
        self.assertIsNone(sample.metrics)
        self.assertEqual(observation.counter_reset_count, 1)

    def test_protocol_type_change_marks_identity_change(self) -> None:
        current_identity = _identity(
            protocol_type=ARPHRD_LOOPBACK,
        )

        previous = _snapshot(
            (_record(),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _record(
                    identity=current_identity,
                    stats=_active_end_stats(),
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.interfaces[0]

        self.assertIs(sample.status, NetworkSampleStatus.IDENTITY_CHANGED)
        self.assertIs(sample.match_method, NetworkMatchMethod.IFINDEX)
        self.assertIsNone(sample.metrics)

    def test_metadata_changes_are_preserved_without_breaking_sampling(self) -> None:
        previous_identity = _identity()

        current_identity = _identity(
            iflink=9,
            mtu=1400,
            operstate=NetworkOperState.DOWN,
            address="00:11:22:33:44:99",
            carrier=False,
        )

        previous = _snapshot(
            (_record(identity=previous_identity),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _record(
                    identity=current_identity,
                    stats=_active_end_stats(),
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.interfaces[0]

        self.assertIs(sample.status, NetworkSampleStatus.SAMPLED)
        self.assertTrue(sample.iflink_changed)
        self.assertTrue(sample.mtu_changed)
        self.assertTrue(sample.operstate_changed)
        self.assertTrue(sample.carrier_changed)
        self.assertTrue(sample.address_changed)
        self.assertIsNotNone(sample.metrics)

    def test_loopback_interface_is_not_filtered(self) -> None:
        loopback_identity = _identity(
            name="lo",
            ifindex=1,
            iflink=1,
            protocol_type=ARPHRD_LOOPBACK,
            mtu=65_536,
            operstate=NetworkOperState.UNKNOWN,
            address="00:00:00:00:00:00",
            carrier=True,
            sysfs_path="/sys/class/net/lo",
        )

        previous = _snapshot(
            (_record(identity=loopback_identity),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _record(
                    identity=loopback_identity,
                    stats=_active_end_stats(),
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.interfaces[0]

        self.assertTrue(sample.identity.is_loopback)
        self.assertIs(sample.status, NetworkSampleStatus.SAMPLED)
        self.assertIsNotNone(sample.metrics)

    def test_zero_traffic_interval_produces_zero_rates(self) -> None:
        previous = _snapshot(
            (_record(),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (_record(),),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        metrics = observation.interfaces[0].metrics

        self.assertIsNotNone(metrics)

        assert metrics is not None

        self.assertEqual(metrics.rx_bytes_per_second, 0.0)
        self.assertEqual(metrics.tx_bytes_per_second, 0.0)
        self.assertEqual(metrics.rx_packets_per_second, 0.0)
        self.assertEqual(metrics.tx_packets_per_second, 0.0)

    def test_invalid_sample_interval_is_rejected(self) -> None:
        previous = _snapshot(
            (_record(),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (_record(),),
            offset_seconds=1.0,
        )

        invalid_values: tuple[object, ...] = (
            True,
            0,
            -1.0,
            math.inf,
            math.nan,
            "1",
        )

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaises(NetworkSamplingError):
                    build_network_observation(
                        previous,
                        current,
                        sample_interval_seconds=invalid_value,
                    )

    def test_unknown_extra_fields_do_not_trigger_reset_logic(self) -> None:
        previous = _snapshot(
            (
                _record(
                    stats=_stats(
                        extra_fields=(100,),
                    ),
                ),
            ),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _record(
                    stats=_stats(
                        extra_fields=(1,),
                    ),
                ),
            ),
            offset_seconds=1.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=1.0,
        )

        sample = observation.interfaces[0]

        self.assertIs(sample.status, NetworkSampleStatus.SAMPLED)
        self.assertEqual(sample.regressed_fields, ())

    def test_serialization_preserves_summary_changes_and_raw_endpoints(
        self,
    ) -> None:
        previous_identity = _identity()

        current_identity = _identity(
            operstate=NetworkOperState.DOWN,
            carrier=False,
        )

        previous = _snapshot(
            (_record(identity=previous_identity),),
            offset_seconds=0.0,
        )

        current = _snapshot(
            (
                _record(
                    identity=current_identity,
                    stats=_active_end_stats(),
                ),
            ),
            offset_seconds=2.0,
        )

        observation = build_network_observation(
            previous,
            current,
            sample_interval_seconds=2.0,
        )

        self.assertIsInstance(observation, NetworkObservation)

        payload = observation.to_dict()

        self.assertEqual(
            payload["summary"],
            {
                "interface_sample_count": 1,
                "sampled_count": 1,
                "appeared_count": 0,
                "disappeared_count": 0,
                "counter_reset_count": 0,
                "identity_changed_count": 0,
                "identity_unverified_count": 0,
                "renamed_count": 0,
                "start_identity_failure_count": 0,
                "end_identity_failure_count": 0,
            },
        )

        interfaces = payload["interfaces"]

        self.assertIsInstance(interfaces, list)

        assert isinstance(interfaces, list)

        interface_payload = interfaces[0]

        self.assertEqual(interface_payload["status"], "sampled")
        self.assertEqual(interface_payload["match_method"], "ifindex")
        self.assertTrue(interface_payload["changes"]["operstate_changed"])
        self.assertTrue(interface_payload["changes"]["carrier_changed"])
        self.assertIsNotNone(interface_payload["start_record"])
        self.assertIsNotNone(interface_payload["end_record"])
        self.assertIsNotNone(interface_payload["metrics"])


if __name__ == "__main__":
    unittest.main()
