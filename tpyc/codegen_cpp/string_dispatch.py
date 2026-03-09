"""String dispatch optimization for match/case and enum lookup.

Analyzes a set of string literals to find the best single discriminator
(string length or character at a fixed position) that partitions the
strings into the smallest possible buckets for switch-based dispatch.
"""

from __future__ import annotations

# Minimum number of unguarded string literal cases to trigger switch dispatch
STRING_SWITCH_THRESHOLD = 5


def find_best_discriminator(
    strings: list[str],
) -> tuple[str, int | None, dict[int, list[str]]]:
    """Find the best discriminator for switch-based string dispatch.

    Args:
        strings: List of distinct string literals to dispatch on.

    Returns:
        (kind, param, buckets) where:
        - kind: "length" or "char_at"
        - param: None for length, character position index for char_at
        - buckets: maps discriminator value -> list of strings
          For "length": value is string length.
          For "char_at": value is ord(char) at the given position.
    """
    if not strings:
        return ("length", None, {})

    # Candidate 1: string length
    best_kind = "length"
    best_param: int | None = None
    best_buckets = _group_by_length(strings)
    best_max = _max_bucket(best_buckets)

    if best_max <= 1:
        return (best_kind, best_param, best_buckets)

    # Candidate 2: character at position i (only positions reachable by all strings)
    min_len = min(len(s) for s in strings)
    for i in range(min_len):
        buckets = _group_by_char_at(strings, i)
        max_b = _max_bucket(buckets)
        if max_b < best_max:
            best_kind = "char_at"
            best_param = i
            best_buckets = buckets
            best_max = max_b
            if best_max <= 1:
                break

    return (best_kind, best_param, best_buckets)


def _group_by_length(strings: list[str]) -> dict[int, list[str]]:
    buckets: dict[int, list[str]] = {}
    for s in strings:
        buckets.setdefault(len(s), []).append(s)
    return buckets


def _group_by_char_at(strings: list[str], pos: int) -> dict[int, list[str]]:
    buckets: dict[int, list[str]] = {}
    for s in strings:
        key = ord(s[pos])
        buckets.setdefault(key, []).append(s)
    return buckets


def _max_bucket(buckets: dict[int, list[str]]) -> int:
    if not buckets:
        return 0
    return max(len(v) for v in buckets.values())
