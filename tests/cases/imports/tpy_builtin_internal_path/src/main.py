# Verifies tpy.copy / copy_iter / own_iter / try_parse compile identically
# when imported via tpy._core (internal path) and via `import tpy as t`.
from tpy._core import copy, copy_iter, own_iter, try_parse
from tpy import int32
import tpy as t
from enum import Enum


class Color(Enum):
    RED = 1
    GREEN = 2


class Holder[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def dup(self) -> T:
        return copy(self.value)  # tpyc: ok

    def dup_via_module(self) -> T:
        return t.copy(self.value)  # tpyc: ok


def sum_copied(xs: list[int32]) -> int32:
    total = int32(0)
    for x in copy_iter(xs):  # tpyc: ok
        total += x
    return total


def consume() -> int32:
    xs: list[int32] = [int32(10), int32(20)]
    total = int32(0)
    for x in own_iter(xs):  # tpyc: ok
        total += x
    return total


def main() -> None:
    h: Holder[int32] = Holder(int32(7))
    print(h.dup())
    print(h.dup_via_module())
    print(sum_copied([int32(1), int32(2), int32(3)]))
    print(consume())
    c = try_parse(Color, "RED")  # tpyc: ok
    print(c is not None)
    miss = try_parse(Color, "PURPLE")
    print(miss is None)


main()
