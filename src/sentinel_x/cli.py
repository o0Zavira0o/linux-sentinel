"""Command-line interface for Sentinel-X."""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Sequence

from sentinel_x import __version__


_MINIMUM_PYTHON = (3, 11)


def _build_parser() -> argparse.ArgumentParser:
    """Create and configure the Sentinel-X command-line parser."""

    parser = argparse.ArgumentParser(
        prog="sentinel-x",
        description=(
            "Sentinel-X: a Linux-native research platform for safe autonomous "
            "fault detection, diagnosis, and remediation."
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
            "Check whether the local runtime is suitable "
            "for Sentinel-X development."
        ),
    )

    return parser


def _run_doctor() -> int:
    """Check the minimum local requirements for Sentinel-X."""

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

        print(
            f"[{status}] "
            f"{name}: "
            f"{detail}"
        )

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


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Sentinel-X command-line interface."""

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _run_doctor()

    parser.print_help()

    return 0
