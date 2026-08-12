"""Command-line interface for Sentinel-X."""

from __future__ import annotations

import platform
import signal
import sys
import time
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from types import FrameType

from sentinel_x import __version__
from sentinel_x.config import (
    ConfigError,
    ConfigValidationError,
    LoadedConfig,
    SentinelConfig,
    load_config,
)
from sentinel_x.core import EventBus, SentinelEngine, SentinelEvent
from sentinel_x.observability import (
    BlockDeviceKind,
    CpuSamplingError,
    DiskIoObservation,
    DiskIoObservationError,
    DiskIoSampleStatus,
    FilesystemObservation,
    FilesystemObservationError,
    HostObservation,
    LinuxDiskStatsReader,
    LinuxFilesystemReader,
    LinuxHostReader,
    LinuxNetworkReader,
    LinuxObservationError,
    MemoryObservation,
    NetworkObservation,
    NetworkObservationError,
    NetworkSampleStatus,
    build_disk_io_observation,
    build_filesystem_observation,
    build_host_observation,
    build_memory_observation,
    build_network_observation,
    disk_io_observation_to_event,
    filesystem_observation_to_event,
    host_observation_to_event,
    memory_observation_to_event,
    network_observation_to_event,
    validate_sample_interval,
)
from sentinel_x.storage import EventRecorderError, JsonlEventRecorder


_MINIMUM_PYTHON = (3, 11)


def _add_config_argument(parser: ArgumentParser) -> None:
    """Add the common configuration-file argument."""

    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            "Path to a Sentinel-X TOML configuration file. "
            "If omitted, ./sentinel.toml is discovered when "
            "present; otherwise built-in defaults are used."
        ),
    )


def _build_parser() -> ArgumentParser:
    """Create and configure the Sentinel-X CLI parser."""

    parser = ArgumentParser(
        prog="sentinel-x",
        description=(
            "Sentinel-X: a Linux-native research platform "
            "for safe autonomous fault detection, diagnosis, "
            "and remediation."
        ),
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "doctor",
        help=(
            "Check whether the local runtime is suitable for Sentinel-X development."
        ),
    )

    config_check_parser = subparsers.add_parser(
        "config-check",
        help=("Load and validate Sentinel-X configuration without starting the agent."),
    )

    _add_config_argument(config_check_parser)

    run_parser = subparsers.add_parser(
        "run",
        help="Run the Sentinel-X core engine.",
    )

    _add_config_argument(run_parser)

    run_parser.add_argument(
        "--tick-interval",
        type=float,
        default=None,
        help="Override agent.tick_interval for this run only.",
    )

    observe_host_parser = subparsers.add_parser(
        "observe-host",
        help="Collect one sampled Linux host observation.",
    )

    _add_config_argument(observe_host_parser)

    observe_host_parser.add_argument(
        "--sample-interval",
        metavar="SECONDS",
        type=float,
        default=1.0,
        help=(
            "Delay between sampled CPU, disk I/O, and network counters. "
            "Default: 1.0 second."
        ),
    )

    return parser


def _run_doctor() -> int:
    """Check minimum local requirements for Sentinel-X."""

    is_linux = sys.platform.startswith("linux")
    python_ok = sys.version_info >= _MINIMUM_PYTHON

    checks = (
        (
            "Linux platform",
            is_linux,
            platform.platform(),
        ),
        (
            "Python version",
            python_ok,
            platform.python_version(),
        ),
    )

    print("Sentinel-X environment check")
    print("=" * 28)

    all_ok = True

    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"

        print(f"[{status}] {name}: {detail}")

        all_ok = all_ok and passed

    if all_ok:
        print()
        print("Environment baseline is ready.")
        return 0

    print(
        "\nEnvironment baseline is not ready.",
        file=sys.stderr,
    )

    return 1


def _load_configuration(
    config_path: str | None,
) -> LoadedConfig | None:
    """Load configuration and render operator-facing errors."""

    try:
        return load_config(config_path)

    except ConfigError as exc:
        print(
            f"configuration error: {exc}",
            file=sys.stderr,
        )

        return None


def _apply_run_overrides(
    config: SentinelConfig,
    args: Namespace,
) -> SentinelConfig:
    """Apply explicitly supported CLI runtime overrides."""

    if args.tick_interval is None:
        return config

    updated_agent = replace(
        config.agent,
        tick_interval=args.tick_interval,
    )

    return replace(
        config,
        agent=updated_agent,
    )


def _run_config_check(args: Namespace) -> int:
    """Validate configuration without starting Sentinel-X."""

    loaded = _load_configuration(args.config)

    if loaded is None:
        return 2

    config = loaded.config

    print("Sentinel-X configuration check")
    print("=" * 30)
    print(f"Source: {loaded.source_label}")
    print("Status: VALID")
    print(f"Agent instance: {config.agent.instance_name}")
    print(f"Tick interval: {config.agent.tick_interval:.3f} seconds")
    print(f"Event storage: {'enabled' if config.storage.enabled else 'disabled'}")
    print(f"Storage directory: {loaded.resolve_path(config.storage.directory)}")
    print(f"Flush on write: {config.storage.flush_on_write}")

    return 0


def _print_event(event: SentinelEvent) -> None:
    """Render a concise Sentinel-X event."""

    print(f"[{event.severity.value.upper()}] {event.kind.value}: {event.message}")


def _print_host_observation_summary(
    observation: HostObservation,
) -> None:
    """Render a concise one-shot CPU/load observation summary."""

    cpu = observation.cpu_utilization
    load = observation.load_average

    print("Sentinel-X CPU/load observation")
    print("=" * 31)
    print(f"Host: {observation.identity.hostname}")
    print(f"Logical CPUs: {observation.identity.logical_cpu_count}")
    print(f"Sample interval: {observation.sample_interval_seconds:.3f} seconds")
    print(f"CPU busy: {cpu.busy_percent:.2f}%")
    print(f"CPU user: {cpu.user_percent:.2f}%")
    print(f"CPU system: {cpu.system_percent:.2f}%")
    print(f"CPU iowait: {cpu.iowait_percent:.2f}%")
    print(f"CPU idle: {cpu.idle_percent:.2f}%")
    print(
        "Load average: "
        f"{load.one_minute:.2f} "
        f"{load.five_minutes:.2f} "
        f"{load.fifteen_minutes:.2f}"
    )

    if cpu.iowait_regressed:
        print(
            "CPU note: iowait counter regressed; its sampled delta was clamped to zero."
        )

    if cpu.guest_accounting_adjusted:
        print(
            "CPU note: guest accounting exceeded its containing "
            "user/nice delta and was conservatively adjusted."
        )


def _print_memory_observation_summary(
    observation: MemoryObservation,
) -> None:
    """Render a concise one-shot memory observation summary."""

    memory = observation.utilization

    print()
    print("Sentinel-X memory observation")
    print("=" * 29)
    print(f"Memory available: {memory.available_percent:.2f}%")
    print(f"Memory used estimate: {memory.used_estimate_percent:.2f}%")

    if memory.swap_used_percent is None:
        print("Swap used: not configured")

    else:
        print(f"Swap used: {memory.swap_used_percent:.2f}%")


def _format_optional_percentage(value: float | None) -> str:
    """Render one optional percentage."""

    if value is None:
        return "n/a"

    return f"{value:.2f}%"


def _print_filesystem_observation_summary(
    observation: FilesystemObservation,
) -> None:
    """Render filesystem capacity and data-quality information."""

    print()
    print("Sentinel-X filesystem observation")
    print("=" * 33)
    print(f"Discovered mounts: {observation.discovered_count}")
    print(f"Probed filesystems: {observation.probed_count}")
    print(f"Skipped mounts: {observation.skipped_count}")
    print(f"Probe failures: {observation.failed_count}")

    if observation.filesystems:
        print()
        print("Filesystem utilization:")

    gib = 1024**3

    for entry in observation.filesystems:
        mount = entry.stats.mount
        utilization = entry.utilization

        print(f"- {mount.mount_point} [{mount.fs_type}; {mount.kind.value}]")
        print(
            "  used/total: "
            f"{_format_optional_percentage(utilization.used_percent_of_total)}"
        )
        print(
            "  user capacity used: "
            f"{_format_optional_percentage(utilization.user_capacity_used_percent)}"
        )
        print(f"  available: {entry.stats.available_bytes / gib:.2f} GiB")
        print(
            "  inode used/total: "
            f"{_format_optional_percentage(utilization.inode_used_percent_of_total)}"
        )

    if observation.failures:
        print()
        print("Filesystem probe failures:")

        for failure in observation.failures:
            print(
                f"- {failure.mount_point}: "
                f"{failure.error_type}: "
                f"{failure.error_message}"
            )


def _print_disk_io_observation_summary(
    observation: DiskIoObservation,
) -> None:
    """Render sampled block-device I/O and data-quality information."""

    print()
    print("Sentinel-X disk I/O observation")
    print("=" * 31)
    print(f"Devices: {len(observation.devices)}")
    print(f"Sampled: {observation.sampled_count}")
    print(f"Appeared: {observation.appeared_count}")
    print(f"Disappeared: {observation.disappeared_count}")
    print(f"Counter resets: {observation.counter_reset_count}")
    print(f"Identity changes: {observation.identity_changed_count}")

    whole_disk_samples = tuple(
        sample
        for sample in observation.devices
        if sample.identity.kind is BlockDeviceKind.WHOLE_DISK
    )

    if not whole_disk_samples:
        return

    print()
    print("Whole-device sampled I/O:")

    mib = 1024**2

    for sample in whole_disk_samples:
        print(
            f"- {sample.identity.device_id} "
            f"{sample.identity.name} "
            f"[{sample.status.value}]"
        )

        if sample.status is DiskIoSampleStatus.COUNTER_RESET:
            print(f"  regressed counters: {', '.join(sample.regressed_fields)}")
            continue

        if sample.status is not DiskIoSampleStatus.SAMPLED:
            continue

        metrics = sample.metrics

        assert metrics is not None

        print(f"  read IOPS: {metrics.read_iops:.2f}")
        print(f"  write IOPS: {metrics.write_iops:.2f}")
        print(f"  read throughput: {metrics.read_bytes_per_second / mib:.3f} MiB/s")
        print(f"  write throughput: {metrics.write_bytes_per_second / mib:.3f} MiB/s")
        print(f"  busy estimate: {metrics.io_busy_percent_estimate:.2f}%")
        print(f"  queue estimate: {metrics.weighted_queue_depth_estimate:.3f}")


def _print_network_observation_summary(
    observation: NetworkObservation,
) -> None:
    """Render sampled network-interface metrics and data-quality evidence."""

    print()
    print("Sentinel-X network observation")
    print("=" * 30)
    print(f"Interfaces: {len(observation.interfaces)}")
    print(f"Sampled: {observation.sampled_count}")
    print(f"Appeared: {observation.appeared_count}")
    print(f"Disappeared: {observation.disappeared_count}")
    print(f"Counter resets: {observation.counter_reset_count}")
    print(f"Identity changes: {observation.identity_changed_count}")
    print(f"Identity unverified: {observation.identity_unverified_count}")
    print(f"Renamed: {observation.renamed_count}")
    print(f"Start identity failures: {len(observation.start_identity_failures)}")
    print(f"End identity failures: {len(observation.end_identity_failures)}")

    print()
    print("Interface sampled I/O:")

    kib = 1024

    for sample in observation.interfaces:
        identity = sample.identity
        operstate = "n/a" if identity.operstate is None else identity.operstate.value

        print(
            f"- {identity.name} "
            f"ifindex={identity.ifindex!s} "
            f"[{sample.status.value}] "
            f"state={operstate} "
            f"loopback={identity.is_loopback}"
        )

        if sample.status is NetworkSampleStatus.COUNTER_RESET:
            print(f"  regressed counters: {', '.join(sample.regressed_fields)}")
            continue

        if sample.status is NetworkSampleStatus.IDENTITY_CHANGED:
            if sample.start_record is not None and sample.end_record is not None:
                print(
                    "  identity: "
                    f"{sample.start_record.identity.name}/"
                    f"{sample.start_record.identity.ifindex!s} -> "
                    f"{sample.end_record.identity.name}/"
                    f"{sample.end_record.identity.ifindex!s}"
                )
            continue

        if sample.status is NetworkSampleStatus.IDENTITY_UNVERIFIED:
            print("  identity: unverified; rate metrics suppressed")
            continue

        if sample.status is not NetworkSampleStatus.SAMPLED:
            continue

        metrics = sample.metrics

        assert metrics is not None

        print(
            f"  RX: {metrics.rx_bytes_per_second / kib:.3f} KiB/s "
            f"{metrics.rx_packets_per_second:.2f} pkt/s"
        )
        print(
            f"  TX: {metrics.tx_bytes_per_second / kib:.3f} KiB/s "
            f"{metrics.tx_packets_per_second:.2f} pkt/s"
        )
        print(f"  RX errors/drop: {metrics.rx_errors_delta}/{metrics.rx_dropped_delta}")
        print(f"  TX errors/drop: {metrics.tx_errors_delta}/{metrics.tx_dropped_delta}")

        changes: list[str] = []

        if sample.name_changed:
            changes.append("name")

        if sample.iflink_changed:
            changes.append("iflink")

        if sample.mtu_changed:
            changes.append("mtu")

        if sample.operstate_changed:
            changes.append("operstate")

        if sample.carrier_changed:
            changes.append("carrier")

        if sample.address_changed:
            changes.append("address")

        if changes:
            print(f"  metadata changes: {', '.join(changes)}")


def _run_observe_host(args: Namespace) -> int:
    """Collect, publish, and optionally persist Linux host observations."""

    loaded = _load_configuration(args.config)

    if loaded is None:
        return 2

    try:
        requested_interval = validate_sample_interval(args.sample_interval)

    except CpuSamplingError as exc:
        print(
            f"argument error: {exc}",
            file=sys.stderr,
        )

        return 2

    host_reader = LinuxHostReader()
    filesystem_reader = LinuxFilesystemReader()
    disk_io_reader = LinuxDiskStatsReader()
    network_reader = LinuxNetworkReader()

    try:
        previous = host_reader.read_snapshot()
        host_monotonic_start = time.monotonic()

        previous_disk = disk_io_reader.read_snapshot()
        disk_monotonic_start = time.monotonic()

        previous_network = network_reader.read_snapshot()
        network_monotonic_start = time.monotonic()

        time.sleep(requested_interval)

        current = host_reader.read_snapshot()
        host_elapsed = time.monotonic() - host_monotonic_start

        current_disk = disk_io_reader.read_snapshot()
        disk_elapsed = time.monotonic() - disk_monotonic_start

        current_network = network_reader.read_snapshot()
        network_elapsed = time.monotonic() - network_monotonic_start

        memory_stats = host_reader.read_memory_stats()
        memory_captured_at = datetime.now(timezone.utc)

        filesystem_report = filesystem_reader.read_report()
        filesystem_captured_at = datetime.now(timezone.utc)

        host_observation = build_host_observation(
            previous,
            current,
            sample_interval_seconds=host_elapsed,
        )

        memory_observation = build_memory_observation(
            memory_stats,
            captured_at=memory_captured_at,
        )

        filesystem_observation = build_filesystem_observation(
            filesystem_report,
            captured_at=filesystem_captured_at,
        )

        disk_io_observation = build_disk_io_observation(
            previous_disk,
            current_disk,
            sample_interval_seconds=disk_elapsed,
        )

        network_observation = build_network_observation(
            previous_network,
            current_network,
            sample_interval_seconds=network_elapsed,
        )

    except KeyboardInterrupt:
        print(
            "host observation interrupted",
            file=sys.stderr,
        )

        return 130

    except (
        LinuxObservationError,
        CpuSamplingError,
        FilesystemObservationError,
        DiskIoObservationError,
        NetworkObservationError,
    ) as exc:
        print(
            f"observation error: {exc}",
            file=sys.stderr,
        )

        return 4

    event_bus = EventBus()
    event_bus.subscribe(_print_event)

    recorder: JsonlEventRecorder | None = None

    if loaded.config.storage.enabled:
        storage_directory = loaded.resolve_path(loaded.config.storage.directory)

        try:
            recorder = JsonlEventRecorder(
                directory=storage_directory,
                instance_name=loaded.config.agent.instance_name,
                flush_on_write=loaded.config.storage.flush_on_write,
            )

        except EventRecorderError as exc:
            print(
                f"storage error: {exc}",
                file=sys.stderr,
            )

            return 3

        event_bus.subscribe(recorder)

        print(f"Run ID: {recorder.run_id}")
        print(f"Event store: {recorder.path}")

    else:
        print("Event store: disabled")

    _print_host_observation_summary(host_observation)
    _print_memory_observation_summary(memory_observation)
    _print_filesystem_observation_summary(filesystem_observation)
    _print_disk_io_observation_summary(disk_io_observation)
    _print_network_observation_summary(network_observation)

    host_event = host_observation_to_event(host_observation)
    memory_event = memory_observation_to_event(memory_observation)
    filesystem_event = filesystem_observation_to_event(filesystem_observation)
    disk_io_event = disk_io_observation_to_event(disk_io_observation)
    network_event = network_observation_to_event(network_observation)

    host_report = event_bus.publish(host_event)
    memory_report = event_bus.publish(memory_event)
    filesystem_event_report = event_bus.publish(filesystem_event)
    disk_io_report = event_bus.publish(disk_io_event)
    network_report = event_bus.publish(network_event)

    reports = (
        host_report,
        memory_report,
        filesystem_event_report,
        disk_io_report,
        network_report,
    )

    for report in reports:
        for failure in report.failures:
            print(
                "event delivery error: "
                f"{failure.handler_name}: "
                f"{failure.error_type}: "
                f"{failure.error_message}",
                file=sys.stderr,
            )

    storage_failed = False

    if recorder is not None:
        if recorder.last_error is not None:
            storage_failed = True

        try:
            recorder.close()

        except EventRecorderError as exc:
            print(
                f"storage error during shutdown: {exc}",
                file=sys.stderr,
            )

            storage_failed = True

    if storage_failed:
        return 3

    if not all(report.succeeded for report in reports):
        return 1

    return 0


def _run_engine(args: Namespace) -> int:
    """Create and run the Sentinel-X core engine."""

    loaded = _load_configuration(args.config)

    if loaded is None:
        return 2

    try:
        config = _apply_run_overrides(
            loaded.config,
            args,
        )

    except ConfigValidationError as exc:
        print(
            f"configuration error: invalid CLI override: {exc}",
            file=sys.stderr,
        )

        return 2

    print(f"Configuration: {loaded.source_label}")
    print(f"Agent instance: {config.agent.instance_name}")

    event_bus = EventBus()
    event_bus.subscribe(_print_event)

    recorder: JsonlEventRecorder | None = None

    if config.storage.enabled:
        storage_directory = loaded.resolve_path(config.storage.directory)

        try:
            recorder = JsonlEventRecorder(
                directory=storage_directory,
                instance_name=config.agent.instance_name,
                flush_on_write=config.storage.flush_on_write,
            )

        except EventRecorderError as exc:
            print(
                f"storage error: {exc}",
                file=sys.stderr,
            )

            return 3

        event_bus.subscribe(recorder)

        print(f"Run ID: {recorder.run_id}")
        print(f"Event store: {recorder.path}")

    else:
        print("Event store: disabled")

    engine = SentinelEngine(
        event_bus=event_bus,
        instance_name=config.agent.instance_name,
    )

    def handle_shutdown_signal(
        signum: int,
        _frame: FrameType | None,
    ) -> None:
        """Convert OS signals into graceful stop requests."""

        try:
            signal_name = signal.Signals(signum).name

        except ValueError:
            signal_name = str(signum)

        engine.request_stop(reason=f"received {signal_name}")

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    signal.signal(
        signal.SIGINT,
        handle_shutdown_signal,
    )

    signal.signal(
        signal.SIGTERM,
        handle_shutdown_signal,
    )

    storage_failed = False

    try:
        engine.run_forever(tick_interval=config.agent.tick_interval)

    except KeyboardInterrupt:
        engine.request_stop(reason="keyboard interrupt")

        engine.stop()

    finally:
        signal.signal(
            signal.SIGINT,
            previous_sigint,
        )

        signal.signal(
            signal.SIGTERM,
            previous_sigterm,
        )

        if recorder is not None:
            if recorder.last_error is not None:
                print(
                    f"storage error occurred during runtime: {recorder.last_error}",
                    file=sys.stderr,
                )

                storage_failed = True

            try:
                recorder.close()

            except EventRecorderError as exc:
                print(
                    f"storage error during shutdown: {exc}",
                    file=sys.stderr,
                )

                storage_failed = True

    if storage_failed:
        return 3

    return 0


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Run the Sentinel-X command-line interface."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _run_doctor()

    if args.command == "config-check":
        return _run_config_check(args)

    if args.command == "observe-host":
        return _run_observe_host(args)

    if args.command == "run":
        return _run_engine(args)

    parser.print_help()

    return 0
