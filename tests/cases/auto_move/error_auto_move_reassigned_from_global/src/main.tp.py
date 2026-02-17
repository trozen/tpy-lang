# Reassigned local rebound to global alias must error (borrowed storage).
from tpy import Int32, Own


class Point:
    x: Int32


g = Point()
g.x = 99


def consume(p: Own[Point]) -> Int32:
    return p.x


def test() -> Int32:
    p = Point()
    p.x = 10
    p = g  # rebind to global alias -> lvalue reassignment
    return consume(p)  # tpyc: error(/implicit copy/)
