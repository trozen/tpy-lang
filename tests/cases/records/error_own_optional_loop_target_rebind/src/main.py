# An owning for target over an iterator of `Own[P | None]` that the body
# rebinds is refused: the pointer over the step has no Optional payload form
# (BUGS.md#own-optional-loop-target-rebind-rejects).
from typing import Iterator
from tpy import int32, Own


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def steps() -> Iterator[Own[P | None]]:
    yield P(1)
    yield None


def main() -> None:
    for p in steps():  # tpyc: error(/loop\.rebound_own_form/)
        q = p
        p = P(9)
        if q is not None:
            q.n += 10
            print("o", q.n, p.n)
        else:
            print("o none", p.n)


main()
