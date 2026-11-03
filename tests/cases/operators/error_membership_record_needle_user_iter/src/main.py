# A record needle over a user iterator (no `__contains__`) stays refused: the
# iteration loop admits only by-value needles, and a record needle must bind as
# a borrow for CPython's `x is e or x == e` (BUGS.md#in-over-user-iterable).
from __future__ import annotations
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, o: P) -> bool:
        return self.n == o.n


class Ps:
    i: int32
    xs: list[P]

    def __init__(self) -> None:
        self.i = 0
        self.xs = [P(1), P(2)]

    def __iter__(self) -> Ps:
        return self

    def __next__(self) -> P:
        if self.i >= 2:
            raise StopIteration
        self.i += 1
        return self.xs[self.i - 1]


def main() -> None:
    q = P(2)
    ps = Ps()
    print(q in ps)  # tpyc: error(/binop\.shape\.in\.record/)


main()
