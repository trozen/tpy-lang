# A consuming store of a MIXED owned+borrow tuple (an owned element inline
# beside a borrowed one) into a field moves only the OWNED element; the
# borrowed element is copied, never moved out of the object it borrows.
# Regression guard for the lift that moved every pointee, emptying the
# caller's object. Every section mutates the borrowed object afterwards and
# prints what the caller still owns (matches CPython); the field's copy of
# the borrowed element is the warned divergence every owning sink has.
from tpy import int32, Own


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.xs = [1, 2, 3]


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


class Holder:
    t: tuple[Box, Box]

    def __init__(self) -> None:
        self.t = (Box(0), Box(0))


class Ctor:
    t: tuple[Box, Box]

    def __init__(self, p: tuple[Own[Box], Box]) -> None:
        # constructor: a mixed PARAM stored at its last use (member-init route).
        p[1].n += 1
        self.t = p  # tpyc: warning(/tuple element 1/)


# free function: a mixed LOCAL stored at its last use.
def field_local(h: Holder, b: Box) -> None:
    t = make_mixed(b)
    h.t = t  # tpyc: warning(/tuple element 1/)


# method: a mixed PARAM written through (so its borrowed element is a mutable
# pointer) and stored at its last use.
class Sink:
    t: tuple[Box, Box]

    def __init__(self) -> None:
        self.t = (Box(0), Box(0))

    def put(self, p: tuple[Own[Box], Box]) -> None:
        p[1].n += 10
        self.t = p  # tpyc: warning(/tuple element 1/)


# inverse: an ALL-BORROW local stored into a field copies both elements and
# moves nothing (no owned element to move).
def field_borrow(h: Holder, c: Box, d: Box) -> None:
    t = (c, d)
    h.t = t  # tpyc: warning(/tuple element 0/) warning(/tuple element 1/)


def main() -> None:
    b = Box(10)
    h = Holder()
    field_local(h, b)
    b.xs.append(4)
    print("field_local", len(b.xs), b.n, h.t[0].n)

    b2 = Box(20)
    s = Sink()
    s.put((Box(1), b2))
    b2.xs.append(4)
    print("method_param", len(b2.xs), b2.n, s.t[0].n)

    b3 = Box(30)
    c = Ctor((Box(1), b3))
    b3.xs.append(4)
    print("ctor_param", len(b3.xs), b3.n, c.t[0].n)

    c1 = Box(1)
    d1 = Box(2)
    h2 = Holder()
    field_borrow(h2, c1, d1)
    c1.xs.append(4)
    print("field_borrow", len(c1.xs), len(d1.xs), h2.t[0].n, h2.t[1].n)


main()
