"""Exact decoding helpers for low-level ``systemctl show`` string arrays."""

from __future__ import annotations

_SHELL_QUOTE_REQUIRED = frozenset("\"\\`$*?['()<>|&;")
_NAMED_C_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}
_DIRECT_ESCAPES = frozenset(('"', "\\", "`", "$"))


class SystemctlStringArrayDecodeError(ValueError):
    """Raised when a systemctl string-array property is not canonically encoded."""


def decode_systemctl_string_array(value: str) -> tuple[str, ...]:
    """Decode the space-separated ``shell_maybe_quote(..., 0)`` representation.

    systemctl renders D-Bus arrays of strings by applying systemd's
    ``shell_maybe_quote`` to each element and joining elements with one space.
    This decoder intentionally accepts that concrete representation rather than
    general shell syntax, so parsing does not broaden the trusted protocol.
    """

    if not isinstance(value, str):
        raise TypeError("value must be a string")
    if value == "":
        return ()

    words: list[str] = []
    index = 0
    length = len(value)

    while index < length:
        if value[index] == " ":
            raise SystemctlStringArrayDecodeError(
                "unexpected empty or repeated array entry"
            )

        if value[index] == '"':
            word, index = _decode_quoted_word(value, index + 1)
        else:
            word, index = _decode_unquoted_word(value, index)

        if word == "":
            raise SystemctlStringArrayDecodeError("empty array entries are unsupported")
        words.append(word)

        if index == length:
            break
        if value[index] != " ":
            raise SystemctlStringArrayDecodeError(
                "array entries must be separated by one space"
            )
        index += 1
        if index == length or value[index] == " ":
            raise SystemctlStringArrayDecodeError(
                "unexpected empty or repeated array entry"
            )

    return tuple(words)


def _decode_unquoted_word(value: str, start: int) -> tuple[str, int]:
    index = start
    length = len(value)

    while index < length and value[index] != " ":
        character = value[index]
        if character.isspace() or character in _SHELL_QUOTE_REQUIRED:
            raise SystemctlStringArrayDecodeError(
                "unquoted array entry contains a character systemd would quote"
            )
        if ord(character) < 32 or ord(character) == 127:
            raise SystemctlStringArrayDecodeError(
                "unquoted array entry contains a control character"
            )
        index += 1

    return value[start:index], index


def _decode_quoted_word(value: str, start: int) -> tuple[str, int]:
    decoded: list[str] = []
    index = start
    length = len(value)

    while index < length:
        character = value[index]
        if character == '"':
            return "".join(decoded), index + 1
        if character != "\\":
            if ord(character) < 32 or ord(character) == 127:
                raise SystemctlStringArrayDecodeError(
                    "quoted array entry contains an unescaped control character"
                )
            decoded.append(character)
            index += 1
            continue

        index += 1
        if index >= length:
            raise SystemctlStringArrayDecodeError(
                "quoted array entry ends with an incomplete escape"
            )

        escaped = value[index]
        if escaped in _DIRECT_ESCAPES:
            decoded.append(escaped)
            index += 1
            continue
        if escaped in _NAMED_C_ESCAPES:
            decoded.append(_NAMED_C_ESCAPES[escaped])
            index += 1
            continue
        if escaped in "01234567":
            if index + 2 >= length:
                raise SystemctlStringArrayDecodeError(
                    "quoted array entry contains an incomplete octal escape"
                )
            octal = value[index : index + 3]
            if any(character not in "01234567" for character in octal):
                raise SystemctlStringArrayDecodeError(
                    "quoted array entry contains an invalid octal escape"
                )
            decoded.append(chr(int(octal, 8)))
            index += 3
            continue

        raise SystemctlStringArrayDecodeError(
            "quoted array entry contains an unsupported escape"
        )

    raise SystemctlStringArrayDecodeError("unterminated quoted array entry")
