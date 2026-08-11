"""Command-line interface for Sentinel-X."""

from __future__ import annotations

import platform
import signal
import sys
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from types import FrameType

from sentinel_x import __version__
from sentinel_x.core import (
    EventBus,
    SentinelEngine,
    SentinelEvent,
)


_MINIMUM_PYTHON = (3, 11)


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

    subparsers = parser.add_subparsers(
        dest="command"
    )

    subparsers.add_parser(
        "doctor",
        help=(
            "Check whether the local runtime is suitable "
            "for Sentinel-X development."
        ),
    )

    run_parser = subparsers.add_parser(
        "run",
        help=(
            "Run the Phase-0 Sentinel-X core engine."
        ),
    )

    run_parser.add_argument(
        "--tick-interval",
        type=float,
        default=0.5,
        help=(
            "Core loop wake-up interval in seconds. "
            "Default: 0.5"
        ),
    )

    return parser


def _run_doctor() -> int:
    """Check minimum local requirements for Sentinel-X."""

    is_linux = sys.platform.startswith(
        "linux"
    )

    python_ok = (
        sys.version_info
        >= _MINIMUM_PYTHON
    )

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

    print(
        "Sentinel-X environment check"
    )

    print(
        "=" * 28
    )

    all_ok = True

    for (
        name,
        passed,
        detail,
    ) in checks:
        status = (
            "PASS"
            if passed
            else "FAIL"
        )

        print(
            f"[{status}] "
            f"{name}: "
            f"{detail}"
        )

        all_ok = (
            all_ok
            and passed
        )

    if all_ok:
        print()

        print(
            "Environment baseline is ready."
        )

        return 0

    print(
        "\nEnvironment baseline is not ready.",
        file=sys.stderr,
    )

    return 1


def _print_event(
    event: SentinelEvent,
) -> None:
    """Render a concise event during Phase-0 development."""

    print(
        f"[{event.severity.value.upper()}] "
        f"{event.kind.value}: "
        f"{event.message}"
    )


def _run_engine(
    args: Namespace,
) -> int:
    """Create and run the Phase-0 Sentinel-X engine."""

    if args.tick_interval <= 0:
        print(
            "error: --tick-interval must "
            "be greater than zero",
            file=sys.stderr,
        )

        return 2

    event_bus = EventBus()

    event_bus.subscribe(
        _print_event
    )

    engine = SentinelEngine(
        event_bus=event_bus
    )

    def handle_shutdown_signal(
        signum: int,
        _frame: FrameType | None,
    ) -> None:
        """Convert OS signals into graceful stop requests."""

        try:
            signal_name = signal.Signals(
                signum
            ).name

        except ValueError:
            signal_name = str(
                signum
            )

        engine.request_stop(
            reason=(
                f"received {signal_name}"
            )
        )

    previous_sigint = (
        signal.getsignal(
            signal.SIGINT
        )
    )

    previous_sigterm = (
        signal.getsignal(
            signal.SIGTERM
        )
    )

    signal.signal(
        signal.SIGINT,
        handle_shutdown_signal,
    )

    signal.signal(
        signal.SIGTERM,
        handle_shutdown_signal,
    )

    try:
        engine.run_forever(
            tick_interval=(
                args.tick_interval
            )
        )

    except KeyboardInterrupt:
        engine.request_stop(
            reason="keyboard interrupt"
        )

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

    return 0


def main(
    argv: Sequence[str] | None = None,
) -> int:
    """Run the Sentinel-X command-line interface."""

    parser = _build_parser()

    args = parser.parse_args(
        argv
    )

    if args.command == "doctor":
        return _run_doctor()

    if args.command == "run":
        return _run_engine(
            args
        )

    parser.print_help()

    return 0
