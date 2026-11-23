# A fresh f-string at a generator's structural `Iterable[char]` parameter is
# refused outside a generator or coroutine body (docs/LANGUAGE_FEATURES.md
# "Built-in `Iterable[T]` conformance";
# BUGS.md#plain-fresh-text-protocol-slot-refused).
from typing import Iterable, Iterator
from tpy import char


def each(xs: Iterable[char]) -> Iterator[char]:
    for c in xs:
        yield c


def count(n: int) -> int:
    a = 0
    # a plain function's loop head
    for c in each(f"ab{n}"):  # tpyc: error(/call.arg_shape.protocol.static/)
        a += 1
    return a


def main() -> None:
    print(count(1))


main()
