"""String dispatch optimization for match/case and enum lookup.

Analyzes a set of string literals to find the best single discriminator
(string length or character at a fixed position) that partitions the
strings into the smallest possible buckets for switch-based dispatch.

All bucketing operates on the UTF-8 byte encoding: the emitted C++
switches on std::string_view::size() (bytes) and on the unsigned-char
value at a byte index, so compile-time keys must be computed the same
way or non-ASCII literals land in unreachable buckets.
"""

from __future__ import annotations

# Minimum number of unguarded string literal cases to trigger switch dispatch
STRING_SWITCH_THRESHOLD = 5


def discriminator_key(s: str, kind: str, param: int | None) -> int:
    """The switch key for `s` under a discriminator, matching the runtime
    byte-wise C++ tests (UTF-8 length / byte value at byte position)."""
    encoded = s.encode("utf-8")
    if kind == "length":
        return len(encoded)
    assert param is not None
    return encoded[param]


def case_label(disc_value: int, kind: str) -> str:
    """C++ case label for a discriminator value. char_at values are byte
    values: printable ASCII renders as a char literal for readability,
    anything else (UTF-8 lead/continuation bytes, controls) as a number."""
    if kind == "char_at" and 32 <= disc_value < 127:
        ch = chr(disc_value)
        if ch in ("'", "\\"):
            return f"'\\{ch}'"
        return f"'{ch}'"
    return str(disc_value)


def find_best_discriminator(
    strings: list[str],
) -> tuple[str, int | None, dict[int, list[str]]]:
    """Find the best discriminator for switch-based string dispatch.

    Args:
        strings: List of distinct string literals to dispatch on.

    Returns:
        (kind, param, buckets) where:
        - kind: "length" or "char_at"
        - param: None for length, byte position index for char_at
        - buckets: maps discriminator value -> list of strings
          For "length": value is UTF-8 byte length.
          For "char_at": value is the byte at the given position.
    """
    if not strings:
        return ("length", None, {})

    encoded = [s.encode("utf-8") for s in strings]

    # Candidate 1: byte length
    best_kind = "length"
    best_param: int | None = None
    best_buckets = _group_by_length(strings, encoded)
    best_max = _max_bucket(best_buckets)

    if best_max <= 1:
        return (best_kind, best_param, best_buckets)

    # Candidate 2: byte at position i (only positions reachable by all strings)
    min_len = min(len(b) for b in encoded)
    for i in range(min_len):
        buckets = _group_by_byte_at(strings, encoded, i)
        max_b = _max_bucket(buckets)
        if max_b < best_max:
            best_kind = "char_at"
            best_param = i
            best_buckets = buckets
            best_max = max_b
            if best_max <= 1:
                break

    return (best_kind, best_param, best_buckets)


def _group_by_length(
    strings: list[str], encoded: list[bytes],
) -> dict[int, list[str]]:
    buckets: dict[int, list[str]] = {}
    for s, b in zip(strings, encoded):
        buckets.setdefault(len(b), []).append(s)
    return buckets


def _group_by_byte_at(
    strings: list[str], encoded: list[bytes], pos: int,
) -> dict[int, list[str]]:
    buckets: dict[int, list[str]] = {}
    for s, b in zip(strings, encoded):
        buckets.setdefault(b[pos], []).append(s)
    return buckets


def _max_bucket(buckets: dict[int, list[str]]) -> int:
    if not buckets:
        return 0
    return max(len(v) for v in buckets.values())
