# A returned nested def capturing a rebound tuple that owns an element: it
# would keep pointers into the function's own element storage, so it is
# refused at the def (BUGS.md#ptr-tuple-capture-and-binder-rejects).
from typing import Callable

from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def escaping(v: Box) -> Callable[[], int32]:
    p = make_mixed(1, v)
    p = make_mixed(5, v)

    # The nested def escapes through the return.
    def k() -> int32:  # tpyc: error(/a closure that outlives 'escaping' captures 'p', which keeps objects of its own in storage of 'escaping' itself/)
        return p[0].n * 100 + p[1].n
    return k


g = escaping(Box(8))
print(g())
