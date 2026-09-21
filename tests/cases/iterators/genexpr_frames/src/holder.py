# The cross-module half of iterators/genexpr_frames: frames that are templates
# (a capture, an `Fn` param) and that another module has to instantiate.
from typing import Iterator
from tpy import int32, Fn


class Holder:
    total: int32

    def __init__(self, n: int32) -> None:
        self.total = n

    def small(self, xs: list[int32]) -> int32:
        # a header-inline method: the importer's .cpp creates this frame.
        return sum(x * self.total for x in xs)  # tpyc: ok


def mapped(f: Fn[[int32], int32], xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield f(x)
