# A nested class sub-pattern is admitted on the Optional chain for its
# CONDITION only: the chain's emit passes no base-name map, so a name bound
# BELOW a nested pattern would have no base to read. The flat spelling
# (`case Outer(inner=i)`) and the condition-only nesting both work; only this
# one rejects (tests/cases/match/nested_field_cond_tiers).
from typing import Optional

from tpy import Own, int32


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Outer:
    inner: Inner

    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner


def f(o: Optional[Outer]) -> int32:
    match o:  # tpyc: error(/stmt\.match/)
        case Outer(inner=Inner(v=n)):
            return n
        case None:
            return -1
        case _:
            return 0


def main() -> None:
    print(f(Outer(Inner(1))))


main()
