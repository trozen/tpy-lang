# An element a rebound tuple owns on one path and borrows on another, passed to
# an `Own` parameter: refused with the copy that compiles, as the `Own` return
# is (BUGS.md#rebound-tuple-borrowed-path-own-return).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def mk(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def take(b: Own[Box]) -> int32:  # tpyc: warning(/Own\[Box\] param 'b' is never consumed/)
    return b.n


def f(v: Box, w: Box, c: bool) -> None:
    p = (w, v)
    if c:
        p = mk(1, v)
    # Element 0 is 'w' on one path and the call's own object on the other.
    print(take(p[0]))  # tpyc: warning(/copies Box into owned storage; use copy\(\) to make this explicit/) error(/element 0 of 'p' is an object of its own on some paths reaching here and borrowed from other storage on others.*use copy\(p\[0\]\) there instead/)


f(Box(3), Box(4), False)
