# A generator factory METHOD whose rvalue receiver is a plain CALL, inside a
# resumable body. The receiver hoist that seats a factory's temporary receiver
# on a field of the enclosing frame covers only the ctor slice the sync
# receiver lift covers, so this shape keeps rejecting exactly as it does in a
# sync caller -- a hoist must fix a dangle, not widen what the language accepts.
from typing import Iterator

from tpy import Int32, Own


class Summer:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def pair(self, xs: list[Int32]) -> Iterator[Int32]:
        yield self.base + xs[0]
        yield self.base + xs[len(xs) - 1]


def make_summer(base: Int32) -> Own[Summer]:
    return Summer(base)


# The reject is reported at the body's owner, so the annotation sits here.
def outer(xs: list[Int32]) -> Iterator[Int32]:  # tpyc: error(/not yet supported/)
    yield 0
    # The receiver is a call rvalue, not a ctor rvalue: no lift in either
    # position, so the for head is rejected rather than silently dangling.
    for v in make_summer(100).pair(xs):
        yield v


def main() -> None:
    for v in outer([1, 2]):
        print("v:", v)


main()
