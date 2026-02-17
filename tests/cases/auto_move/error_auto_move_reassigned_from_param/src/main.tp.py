# Reassigned local rebound to param alias must error (borrowed storage).
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def test(a: Point) -> Int32:
    p = Point()
    p.x = 10
    p = a  # rebind to param alias -> lvalue reassignment
    return consume(p)  # tpyc: error(/implicit copy/)
