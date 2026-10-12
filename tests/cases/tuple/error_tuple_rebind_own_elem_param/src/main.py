# A rebound tuple whose element is its own on one path and borrowed on another,
# passed whole to a parameter owning that element only: refused with the tuple
# that copies the owned position and hands the borrowed one on
# (BUGS.md#rebound-tuple-borrowed-path-own-return).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def mk(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def take(t: tuple[Own[Box], Box]) -> int32:  # tpyc: warning(/Own\[Box\] element 0 of tuple param 't' is never consumed/)
    t[1].n += 100
    a, b = t
    return a.n


def f(v: Box, w: Box, c: bool) -> None:
    p = (w, v)
    if c:
        p = mk(1, v)
    # Element 0 is 'w' on one path and the call's own object on the other.
    print(take(p), v.n, w.n)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/) error(/element 0 of 'p' is an object of its own on some paths reaching here and borrowed from other storage on others.*use \(copy\(p\[0\]\), p\[1\]\) there instead/)


f(Box(3), Box(4), False)
