# The adjacent shape the borrow-form `yield <call>` relay must NOT claim: a
# callee whose tuple return is the STORAGE form (a per-element `Own`) hands
# back a temporary, so relaying it bare into the borrow-form yield slot would
# hand out pointers into storage that dies at the yield.
from typing import Iterator

from tpy import Own, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def mk(i: int32) -> tuple[int32, Own[P]]:
    return (i, P(i))


# the reject is located at the frame's def, not at the yield inside it
def g() -> Iterator[tuple[int32, P]]:  # tpyc: error(/res\.btuple_yield_source/)
    yield mk(0)


def main() -> None:
    for i, p in g():
        print("g", i, p.x)


main()
