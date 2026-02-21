# Borrowed-param alias at last use must still error (T& reference, not owned).
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def test(a: Point) -> Int32:
    b = a  # lvalue init -> T& reference (Tier 2)
    return consume(b)  # tpyc: warning(/copies.*into owned storage/)
