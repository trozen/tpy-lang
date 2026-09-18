from typing import Iterator
from tpy import int32


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


class Bag:
    cells: list[Cell]

    def __init__(self) -> None:
        self.cells = []

    # Method generator: its __next__ body is inline in gens_inl.hpp.
    def evens(self) -> Iterator[Cell]:
        for c in self.cells:
            if c.v % 2 == 0:
                yield c


# Free generator over a range and a list; __next__ inline in gens_inl.hpp.
def squares(n: int32, skip: list[int32]) -> Iterator[int32]:
    for i in range(n):
        if i not in skip:
            yield i * i


# Past the inline size limit (try/finally): __next__ stays out-of-line in
# gens.cpp, and main still calls it from another module.
def guarded(xs: list[int32], log: list[str]) -> Iterator[int32]:
    try:
        for x in xs:
            yield x
    finally:
        log.append("guarded done")


# Its comment quotes its own C++ declarator; the source echo copies that text
# above the definition, where the `inline` prefix must not land:
# std::expected<int32_t, ::tpy::StopIteration> __gen_echoed::__next__()
def echoed(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i + 1
