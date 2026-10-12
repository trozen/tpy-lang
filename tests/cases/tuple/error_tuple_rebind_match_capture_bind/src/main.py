# A match capture writing the binding of a rebound tuple that owns an element:
# the capture does not place the elements one by one, so it is refused
# (BUGS.md#ptr-tuple-capture-and-binder-rejects).
from typing import Optional

from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def maybe(n: int32, b: Box) -> Optional[tuple[Own[Box], Box]]:
    return (Box(n), b)


def f(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    match maybe(5, v):
        case None:
            pass
        # The capture is the name 'p' already bound above.
        case p:  # tpyc: error(/binding 'p' to a match capture is not yet supported here: .p. keeps objects of its own element by element, and this binding does not place them one by one/)
            pass
    print(p[0].n)


f(Box(3))
