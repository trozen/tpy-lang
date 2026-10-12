# A closure that can outlive the body, capturing a rebound tuple that owns an
# element, would keep pointers into the body's own element storage: refused
# at the closure (BUGS.md#ptr-tuple-capture-and-binder-rejects).
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
    # The lambda copies its captures and is returned.
    return lambda: p[0].n * 100 + p[1].n  # tpyc: error(/a closure that outlives 'escaping' captures 'p', which keeps objects of its own in storage of 'escaping' itself.*bind copies of its elements \(`p0 = copy\(p\[0\]\)`, with `copy` from `tpy`\) and capture those/)


print(escaping(Box(8))())
