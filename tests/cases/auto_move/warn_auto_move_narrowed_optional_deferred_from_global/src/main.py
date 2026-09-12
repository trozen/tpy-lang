# Deferred Optional local assigned from global alias must reject auto-move.
from tpy import int32, Own


class Point:
    x: int32


g: Point | None = Point()
g.x = int32(99)


def consume(p: Own[Point]) -> int32:
    return p.x


def test() -> int32:
    q: Point | None
    q = g
    assert q is not None
    return consume(q)  # tpyc: warning(/copies.*into owned storage/)
