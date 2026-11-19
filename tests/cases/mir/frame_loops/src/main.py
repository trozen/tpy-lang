# MIR lowers a `for` loop whose body suspends (a generator's decomposed loop)
# with the structured loop's own cursor operations, and warns in it as in a
# plain function; the variants live in the part_* modules.
from typing import Iterator

from tpy import int32

import part_shapes
import part_gate
import part_refusals
import part_genexpr


# range_stop: a counted loop over `range(n)` across a yield
def squares_plus(n: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    yield -1
    for i in range(n):
        yield i * i


# range_start_stop: `range(a, b)`
def between(a: int32, b: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    for i in range(a, b):
        yield i


# str_copy: a `str` parameter the frame holds its own copy of; its cursor walks frame storage
def letters(s: str) -> Iterator[int32]:  # tpyc: mir(covered)
    for c in s:
        yield 1 if c == "a" else 0


class Ticker:
    step: int32

    def __init__(self, step: int32) -> None:
        self.step = step

    # method_range: a method generator over a range, reading its receiver at each pass
    def ticks(self, n: int32) -> Iterator[int32]:  # tpyc: mir(covered)
        for i in range(n):
            yield i * self.step


# rebind: the plain loop's warning -- the assignment frees the string the cursor still walks
def rebinds(s: str) -> Iterator[int32]:  # tpyc: mir(conflict /replacement/)
    for c in s:
        yield 1
        s = "other text that is long enough to allocate"  # tpyc: warning(/frees the old value of 's', but the iteration over 's' still walks it/)


def main() -> None:
    print("range_stop:", list(squares_plus(4)))
    print("range_start_stop:", list(between(2, 5)))
    print("str_copy:", sum(letters("banana")))
    print("method_range:", list(Ticker(3).ticks(3)))
    # Only the first step runs: the rebinding after the yield never does.
    first = rebinds("abcdefghijklmnopqrstuvwxyz0123456789")
    print("rebind:", next(first, -1))
    part_shapes.main()
    part_gate.main()
    part_refusals.main()
    part_genexpr.main()


main()
