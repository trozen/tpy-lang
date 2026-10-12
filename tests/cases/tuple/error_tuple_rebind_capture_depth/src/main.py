# A lambda that copies its captures, nested in one that does not, capturing an
# alias of a rebound tuple that owns an element: refused at the inner lambda,
# naming the owner (BUGS.md#ptr-tuple-capture-and-binder-rejects).
from typing import Callable

from tpy import Fn, Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def apply(f: Fn[[], int32]) -> int32:
    return f()


def run(f: Callable[[], int32]) -> int32:
    return f()


def depth(v: Box) -> int32:
    p = make_mixed(1, v)
    p = make_mixed(5, v)
    q = p
    # The inner lambda, at a `Callable` slot, copies 'q'.
    return apply(lambda: run(lambda: q[0].n * 100 + q[1].n))  # tpyc: error(/a closure that outlives 'depth' captures 'q', which may hold the objects 'p' keeps in storage of 'depth' itself; capturing it is not yet supported here; bind copies of its elements \(`q0 = copy\(q\[0\]\)`, with `copy` from `tpy`\) and capture those/)


print(depth(Box(8)))
