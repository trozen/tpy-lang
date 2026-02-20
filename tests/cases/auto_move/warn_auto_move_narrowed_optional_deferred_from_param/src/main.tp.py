# Deferred Optional local assigned from param alias must still reject auto-move.
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def test(a: Point | None) -> Int32:
    q: Point | None
    q = a
    assert q is not None
    return consume(q)  # tpyc: warning(/copies.*into owned storage/)
