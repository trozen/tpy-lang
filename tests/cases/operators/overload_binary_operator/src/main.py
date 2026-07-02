# Regression: an operand-polymorphic binary operator written as a
# typing.overload set (typed stubs + one shared impl) must compile. Codegen
# used to emit an extra friend operator for the impl's own (union-operand)
# signature, forwarding to a specialized-away `__sub__(variant)` -- so ANY
# @overload operator with a shared impl failed to build. This is the shape
# datetime's `date - date -> timedelta` / `date - timedelta -> date` needs.
from __future__ import annotations
from typing import overload
from tpy import ValueType


class Delta(ValueType):
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __add__(self, other: Delta) -> Delta:   # monomorphic operator on the same class
        return Delta(self.n + other.n)


class Day(ValueType):
    ordinal: int

    def __init__(self, ordinal: int) -> None:
        self.ordinal = ordinal

    # Operand-polymorphic: Day - Day -> Delta, Day - Delta -> Day.
    @overload
    def __sub__(self, other: Day) -> Delta: ...
    @overload
    def __sub__(self, other: Delta) -> Day: ...
    def __sub__(self, other: Day | Delta) -> Delta | Day:
        if isinstance(other, Delta):
            return Day(self.ordinal - other.n)
        return Delta(self.ordinal - other.ordinal)

    def __add__(self, other: Delta) -> Day:   # monomorphic operator coexists with the overloaded one
        return Day(self.ordinal + other.n)


def main() -> None:
    a = Day(10)
    b = Day(3)

    diff = a - b            # tpyc: type(Delta)
    print(diff.n)           # 7  -- typechecks only if `diff` narrowed to Delta

    moved = a - Delta(2)     # tpyc: type(Day)
    print(moved.ordinal)    # 8  -- typechecks only if `moved` narrowed to Day

    later = a + Delta(5)     # monomorphic __add__ still works on the same class
    print(later.ordinal)    # 15

    total = Delta(4) + Delta(6)
    print(total.n)          # 10


main()
