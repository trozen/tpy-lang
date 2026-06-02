# Regression: a narrowed value-union var rebound via tuple-unpack inside the
# narrowed block loses its narrowing, so the resume case after the rebind does
# not re-cast the stale variant alternative (would throw bad_variant_access).
from typing import Iterator


def remake() -> tuple[int | str, int]:
    return ("hello", 9)


def gen(a: int | str) -> Iterator[str]:
    if isinstance(a, int):
        yield "int:" + str(a + 1)
        a, n = remake()
        yield "rebound"
        if isinstance(a, str):
            yield "str:" + a
        else:
            yield "still-int"


def main() -> None:
    for s in gen(5):
        print(s)


main()
