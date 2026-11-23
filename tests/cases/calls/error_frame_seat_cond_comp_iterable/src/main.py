# A conditional between two comprehensions at a generator's `Iterable`
# parameter in an ordinary function is not lowered yet
# (BUGS.md#cond-comp-at-protocol-param-rejects); in a frame head the frame
# holds it (frame_seat_plan_awaited).
from typing import Iterable, Iterator
from tpy import int32


def g_it(it: Iterable[int32]) -> Iterator[int32]:
    for x in it:
        yield x


def outer(c: bool) -> int32:
    # the select of two comprehensions at the protocol parameter
    return sum(g_it([x for x in range(3)] if c else [x * 2 for x in range(3)]))  # tpyc: error(/not yet supported by C\+\+ code generation/)


def main() -> None:
    print(outer(True), outer(False))


main()
