# Reassigned local with rvalue on one branch and lvalue on another must error.
# The lvalue_reassigned tracking prevents moving from potentially borrowed storage.
from tpy import int32, Own


class Point:
    x: int32


def consume(p: Own[Point]) -> int32:
    return p.x


def test(a: Point, cond: bool) -> int32:
    p = Point()
    p.x = 10
    if cond:
        p = Point()  # rvalue reassignment
        p.x = 20
    else:
        p = a  # lvalue reassignment -> borrowed
    return consume(p)  # tpyc: warning(/copies.*into owned storage/)
