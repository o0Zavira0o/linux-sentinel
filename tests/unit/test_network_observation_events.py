"""Unit tests for Sentinel-X sampled network observation events."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone

from sentinel_x.core import EventBus, EventKind
from sentinel_x.observability import (
    NETWORK_OBSERVATION_SOURCE,
    NETWORK_OBSERVATION_TYPE,
    NetworkInterfaceIdentity,
    NetworkInterfaceRecord,
    NetworkInterfaceStats,
    NetworkOperState,
    NetworkStatsSnapshot,
    build_network_observation,
    network_observation_to_event,
)
from sentinel_x.storage import JsonlEventRecorder


def _network_observation():
    identity = NetworkInterfaceIdentity(
        name="eth0",
        ifindex=2,
        iflink=2,
        protocol_type=1,
        mtu=1500,
        operstate=NetworkOperState.UP,
        address="00:11:22:33:44:55",
        carrier=True,
        sysfs_path="/sys/class/net/eth0",
    )

    previous_stats = NetworkInterfaceStats(
        rx_bytes=1000,
        rx_packets=10,
        rx_errors=1,
        rx_dropped=2,
        rx_fifo_errors=3,
        rx_frame_errors_aggregate=4,
        rx_compressed=5,
        rx_multicast=6,
        tx_bytes=2000,
        tx_packets=20,
        tx_errors=7,
        tx_dropped=8,
        tx_fifo_errors=9,
        tx_collisions=10,
        tx_carrier_errors_aggregate=11,
        tx_compressed=12,
    )

    current_stats = NetworkInterfaceStats(
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

    previous = NetworkStatsSnapshot(
        captured_at=datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc,
        ),
        interfaces=(
            NetworkInterfaceRecord(
                identity=identity,
                stats=previous_stats,
            ),
        ),
        identity_failures=(),
    )

    current = NetworkStatsSnapshot(
        captured_at=datetime(
            2026,
            1,
            1,
            0,
            0,
            2,
            tzinfo=timezone.utc,
        ),
        interfaces=(
            NetworkInterfaceRecord(
                identity=identity,
                stats=current_stats,
            ),
        ),
        identity_failures=(),
    )

    return build_network_observation(
        previous,
        current,
        sample_interval_seconds=2.0,
    )


class NetworkObservationEventTests(unittest.TestCase):
    """Tests for network observation event adaptation and persistence."""

    def test_network_observation_is_converted_to_typed_event(self) -> None:
        observation = _network_observation()

        event = network_observation_to_event(observation)

        self.assertIs(event.kind, EventKind.OBSERVATION)
        self.assertEqual(event.source, NETWORK_OBSERVATION_SOURCE)
        self.assertEqual(event.occurred_at, observation.captured_at)
        self.assertEqual(
            event.attributes["observation_type"],
            NETWORK_OBSERVATION_TYPE,
        )

        summary = event.attributes["summary"]

        self.assertIsInstance(summary, dict)

        assert isinstance(summary, dict)

        self.assertEqual(summary["sampled_count"], 1)
        self.assertEqual(summary["counter_reset_count"], 0)

        interfaces = event.attributes["interfaces"]

        self.assertIsInstance(interfaces, list)

        assert isinstance(interfaces, list)

        metrics = interfaces[0]["metrics"]

        self.assertIsInstance(metrics, dict)

        assert isinstance(metrics, dict)

        self.assertAlmostEqual(metrics["rx_bytes_per_second"], 1000.0)
        self.assertAlmostEqual(metrics["tx_bytes_per_second"], 1500.0)

    def test_network_observation_event_is_persisted_through_event_bus(
        self,
    ) -> None:
        observation = _network_observation()
        event = network_observation_to_event(observation)

        with tempfile.TemporaryDirectory() as tmpdir:
            recorder = JsonlEventRecorder(
                directory=tmpdir,
                instance_name="test-node",
            )

            bus = EventBus()
            bus.subscribe(recorder)

            report = bus.publish(event)

            event_path = recorder.path
            recorder.close()

            lines = event_path.read_text(encoding="utf-8").splitlines()

        self.assertTrue(report.succeeded)
        self.assertEqual(report.delivered, 1)
        self.assertEqual(len(lines), 1)

        payload = json.loads(lines[0])
        attributes = payload["event"]["attributes"]

        self.assertEqual(
            attributes["observation_type"],
            NETWORK_OBSERVATION_TYPE,
        )
        self.assertEqual(attributes["summary"]["sampled_count"], 1)

        interfaces = attributes["interfaces"]

        self.assertEqual(len(interfaces), 1)
        self.assertEqual(interfaces[0]["status"], "sampled")
        self.assertEqual(interfaces[0]["match_method"], "ifindex")
        self.assertIsNotNone(interfaces[0]["start_record"])
        self.assertIsNotNone(interfaces[0]["end_record"])
        self.assertAlmostEqual(
            interfaces[0]["metrics"]["rx_packets_per_second"],
            10.0,
        )


if __name__ == "__main__":
    unittest.main()
