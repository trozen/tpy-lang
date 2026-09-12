# Reassigned local rebound to param alias must error (borrowed storage).
from tpy import int32, Own


class Point:
    x: int32


def consume(p: Own[Point]) -> int32:
    return p.x


def test(a: Point) -> int32:
    p = Point()
    p.x = 10
    p = a  # rebind to param alias -> lvalue reassignment
    return consume(p)  # tpyc: warning(/copies.*into owned storage/)
