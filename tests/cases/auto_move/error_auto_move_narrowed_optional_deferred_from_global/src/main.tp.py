# Deferred Optional local assigned from global alias must reject auto-move.
from tpy import Int32, Own


class Point:
    x: Int32


g: Point | None = Point()
g.x = Int32(99)


def consume(p: Own[Point]) -> Int32:
    return p.x


def test() -> Int32:
    q: Point | None
    q = g
    assert q is not None
    return consume(q)  # tpyc: error(/implicit copy/)
