"""Command-line interface for Sentinel-X."""

from __future__ import annotations

import platform
import signal
import sys
import time
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from dataclasses import replace
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
    CpuSamplingError,
    HostObservation,
    LinuxHostReader,
    LinuxObservationError,
    build_host_observation,
    host_observation_to_event,
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
        help="Collect one sampled Linux host CPU/load observation.",
    )

    _add_config_argument(observe_host_parser)

    observe_host_parser.add_argument(
        "--sample-interval",
        metavar="SECONDS",
        type=float,
        default=1.0,
        help="Delay between CPU counter samples. Default: 1.0 second.",
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
    """Render a concise one-shot host observation summary."""

    cpu = observation.cpu_utilization
    load = observation.load_average

    print("Sentinel-X host observation")
    print("=" * 28)
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


def _run_observe_host(args: Namespace) -> int:
    """Collect, publish, and optionally persist one Linux host observation."""

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

    reader = LinuxHostReader()

    try:
        previous = reader.read_snapshot()
        monotonic_start = time.monotonic()

        time.sleep(requested_interval)

        current = reader.read_snapshot()
        elapsed = time.monotonic() - monotonic_start

        observation = build_host_observation(
            previous,
            current,
            sample_interval_seconds=elapsed,
        )

    except KeyboardInterrupt:
        print(
            "host observation interrupted",
            file=sys.stderr,
        )

        return 130

    except (LinuxObservationError, CpuSamplingError) as exc:
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

    _print_host_observation_summary(observation)

    event = host_observation_to_event(observation)
    report = event_bus.publish(event)

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

    if not report.succeeded:
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
