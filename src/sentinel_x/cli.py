"""Command-line interface for Sentinel-X."""

from __future__ import annotations

import platform
import signal
import sys
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
        help="Run the Phase-0 Sentinel-X core engine.",
    )

    _add_config_argument(run_parser)

    run_parser.add_argument(
        "--tick-interval",
        type=float,
        default=None,
        help="Override agent.tick_interval for this run only.",
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
    """Render a concise event during Phase-0 development."""

    print(f"[{event.severity.value.upper()}] {event.kind.value}: {event.message}")


def _run_engine(args: Namespace) -> int:
    """Create and run the Phase-0 Sentinel-X engine."""

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

    if args.command == "run":
        return _run_engine(args)

    parser.print_help()

    return 0
