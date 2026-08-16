"""Private Phase-5F validation primitives.

This module is intentionally private.  It owns only the small validation surface
shared by the visible evaluation boundary and hidden gold boundary.
"""

from __future__ import annotations

import re

_CASE_ID_PATTERN = re.compile(r"CASE-[0-9]{4}")
_EVIDENCE_REF_PATTERN = re.compile(r"REF-[0-9]{4}")
_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")

MAX_CASE_TASK_CHARS = 4096
MAX_GOLD_GROUP_CHARS = 256


def validate_case_id(value: object) -> str:
    if not isinstance(value, str) or _CASE_ID_PATTERN.fullmatch(value) is None:
        raise ValueError("case_id must match CASE-####")
    return value


def validate_evidence_ref(value: object) -> str:
    if not isinstance(value, str) or _EVIDENCE_REF_PATTERN.fullmatch(value) is None:
        raise ValueError("evidence reference must match REF-####")
    return value


def validate_kind(value: object) -> str:
    if not isinstance(value, str) or _KIND_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "evidence kind must be lowercase snake-like text up to 64 characters"
        )
    return value


def validate_bounded_text(
    value: object,
    *,
    field_name: str,
    max_chars: int,
) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty text")
    if len(value) > max_chars:
        raise ValueError(f"{field_name} exceeds {max_chars} characters")
    if "\x00" in value:
        raise ValueError(f"{field_name} must not contain NUL")
    return value
