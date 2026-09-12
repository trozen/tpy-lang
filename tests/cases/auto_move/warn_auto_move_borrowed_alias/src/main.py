# Borrowed-param alias at last use must still error (T& reference, not owned).
from tpy import int32, Own


class Point:
    x: int32


def consume(p: Own[Point]) -> int32:
    return p.x


def test(a: Point) -> int32:
    b = a  # lvalue init -> T& reference (Tier 2)
    return consume(b)  # tpyc: warning(/copies.*into owned storage/)
