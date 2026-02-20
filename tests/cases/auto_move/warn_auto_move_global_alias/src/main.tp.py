# Global alias at last use must still error (T& reference to global, not owned).
from tpy import Int32, Own


class Point:
    x: Int32


g = Point()
g.x = 42


def consume(p: Own[Point]) -> Int32:
    return p.x


def test() -> Int32:
    q = g  # lvalue init -> T& reference (Tier 2)
    return consume(q)  # tpyc: warning(/copies.*into owned storage/)
