# A rebound tuple whose element is its own on one path and borrowed on
# another, handed whole to an owning sink -- here returned into an
# `Own[tuple]` slot: refused with the tuple of copies that compiles
# (BUGS.md#rebound-tuple-borrowed-path-own-return).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def mk(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def ret_whole(v: Box, w: Box, c: bool) -> Own[tuple[Box, Box]]:
    p = (w, v)
    if c:
        p = mk(1, v)
    # Element 0 is 'w' on one path and the call's own object on the other.
    return p  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/) warning(/copies Box into owned storage \(tuple element 1\)/) error(/element 0 of 'p' is an object of its own on some paths reaching here and borrowed from other storage on others.*use \(copy\(p\[0\]\), copy\(p\[1\]\)\) there instead/)


t = ret_whole(Box(1), Box(2), False)
print(t[0].n)
