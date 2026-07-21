# Str elements yielded through the universal protocol-iterator path
# (Iterable[str] params, narrowed-Optional containers) must stay valid for
# the loop body. Strings longer than the SSO buffer catch a view bound to a
# destroyed temporary (short strings survive in stale stack bytes).

from typing import Iterable

NEEDLE = "transfer-encoding-very-long-key"


def proto_hits(xs: Iterable[str]) -> int:
    hits = 0
    for s in xs:
        if s == NEEDLE:
            hits += 1
    return hits


def narrow_list_hits(xs: list[str] | None) -> int:
    hits = 0
    if xs is not None:
        for s in xs:
            if s == NEEDLE:
                hits += 1
    return hits


def narrow_set_hits(xs: set[str] | None) -> int:
    hits = 0
    if xs is not None:
        for s in xs:
            if s == NEEDLE:
                hits += 1
    return hits


def append_needle(xs: list[str] | None) -> None:
    if xs is not None:
        xs.append(NEEDLE)


def main() -> None:
    data: list[str] = [NEEDLE, "short", "another-string-past-sso-length"]
    print(proto_hits(data))
    print(narrow_list_hits(data))
    print(narrow_set_hits({NEEDLE, "another-string-past-sso-length"}))
    print(narrow_list_hits(None))
    # Callee mutation is visible to the caller: the narrowed-Optional param
    # aliases the caller's list (no copy at the boundary).
    append_needle(data)
    print(narrow_list_hits(data))


main()
