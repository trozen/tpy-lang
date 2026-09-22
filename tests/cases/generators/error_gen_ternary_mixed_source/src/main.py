# A `for` head choosing between an existing object and a fresh one has no one
# capture: the frame would have to own the conditional, which copies the
# lending arm away from its owner. Refused, as the plain-function route does.
from tpy import Own, int32
from typing import Iterator


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def make_cells() -> Own[list[Cell]]:
    return [Cell(50), Cell(60)]


def mixed(xs: list[Cell], flag: bool) -> Iterator[int32]:
    # one arm is the caller's list, the other a temporary.
    for c in (xs if flag else make_cells()):  # tpyc: error(/an existing object and a fresh one/)
        c.v += 100
        yield c.v


def main() -> None:
    a = [Cell(1), Cell(2)]
    for v in mixed(a, True):
        print(v)


main()
