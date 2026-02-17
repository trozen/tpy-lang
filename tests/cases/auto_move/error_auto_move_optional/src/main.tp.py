# Optional non-value local at last use must still error (T* pointer-local).
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def test(a: Point | None) -> Int32:
    q: Point | None = a
    assert q is not None
    return consume(q)  # tpyc: error(/implicit copy/)
