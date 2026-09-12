# Optional non-value local at last use must still error (T* pointer-local).
from tpy import int32, Own


class Point:
    x: int32


def consume(p: Own[Point]) -> int32:
    return p.x


def test(a: Point | None) -> int32:
    q: Point | None = a
    assert q is not None
    return consume(q)  # tpyc: warning(/copies.*into owned storage/)
