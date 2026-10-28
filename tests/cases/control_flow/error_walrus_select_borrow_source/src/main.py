# A walrus bound to a select of references is refused: a select may hold a
# fresh operand in a slot that lives only as long as its statement, which
# the walrus target outlives (BUGS.md#walrus-select-borrow-source-rejects).
# The plain binding `p = a if c else b` compiles. A walrus writing a target
# hoisted out of a `try` or a loop is that plain assignment's write, and a
# select mixing a fresh operand refuses there as the assignment does.
from tpy import int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(a: Rec, b: Rec, c: bool) -> int32:
    if (p := a if c else b).n > 0:  # tpyc: error(/expr\.walrus/)
        p.n += 1
    return a.n + b.n


def main() -> None:
    print(f(Rec(1), Rec(2), True))


main()
