# Global alias at last use must still error (T& reference to global, not owned).
from tpy import int32, Own


class Point:
    x: int32


g = Point()
g.x = 42


def consume(p: Own[Point]) -> int32:
    return p.x


def test() -> int32:
    q = g  # lvalue init -> T& reference (Tier 2)
    return consume(q)  # tpyc: warning(/copies.*into owned storage/)
