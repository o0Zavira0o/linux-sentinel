"""Typed current-boot identity helpers for Sentinel-X systemd evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final, Protocol

_BOOT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{32}$")
_BOOT_ID_PATH: Final[Path] = Path("/proc/sys/kernel/random/boot_id")


class SystemBootIdError(RuntimeError):
    """Raised when the current Linux boot identity cannot be trusted."""


class SystemBootIdReader(Protocol):
    """Callable contract for the currently running Linux boot ID."""

    def __call__(self) -> str:
        """Return the current boot ID as 32 hexadecimal characters."""

        ...


def normalize_boot_id(value: object, *, field_name: str = "boot_id") -> str:
    """Normalize one Linux boot ID to lowercase 32-character hexadecimal form."""

    if not isinstance(value, str):
        raise SystemBootIdError(f"{field_name} must be a string")
    normalized = value.strip().replace("-", "").lower()
    if _BOOT_ID_PATTERN.fullmatch(normalized) is None:
        raise SystemBootIdError(
            f"{field_name} must contain exactly 32 hexadecimal characters"
        )
    return normalized


def read_current_boot_id() -> str:
    """Read and normalize the current Linux boot ID from procfs."""

    try:
        raw_value = _BOOT_ID_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemBootIdError(f"failed to read current boot ID: {exc}") from exc
    return normalize_boot_id(raw_value, field_name="current boot ID")
