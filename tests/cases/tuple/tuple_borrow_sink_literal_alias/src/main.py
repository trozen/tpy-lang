# The inverse of the owning-sink rule: a tuple literal at a BORROWING sink (a
# plain `tuple[...]` param, which the callee only reads through for the
# duration of the call) must keep its borrow-form element slots, so the
# literal's lvalue element ALIASES the caller's object rather than copying it.
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bump_first(p: tuple[Item, int32]) -> None:
    # Borrowing param: writing through the element must reach the caller's
    # object, which is only true if the slot stayed a borrow.
    p[0].n = p[0].n + p[1]


def read_pair(p: tuple[Item, int32]) -> int32:
    return p[0].n * p[1]


def relay[T](p: tuple[T, int32]) -> int32:
    return p[1]


def main() -> None:
    it = Item(5)
    # Subject: the literal's element is a simple lvalue at a borrowing sink.
    bump_first((it, 2))
    print(it.n)
    bump_first((it, 10))
    print(it.n)
    # An RVALUE element at a borrowing sink still routes through the borrow
    # lift over a source tuple whose lifetime covers the call.
    print(read_pair((Item(4), 3)))
    print(relay((it, 3)))


main()
