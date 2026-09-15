# The adjacent shape the borrow-form `yield <call>` relay must NOT claim: an
# argument that needs a hoisted `__tmp_N`. A yield is not a flush point, so the
# temp would have to live in the resume function and die at the suspend with
# the yielded pointers still aimed at it -- the arg gate refuses the temp rows
# here the way it does in a match guard.
from typing import Iterator

from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def mk(p: P) -> tuple[int32, P]:
    return (0, p)


# the reject is located at the frame's def, not at the yield inside it
def g() -> Iterator[tuple[int32, P]]:  # tpyc: error(/call\.arg_shape\.record_f1/)
    yield mk(P(7))


def main() -> None:
    for i, p in g():
        print("g", i, p.x)


main()
