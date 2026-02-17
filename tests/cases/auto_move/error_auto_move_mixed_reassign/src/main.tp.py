# Reassigned local with rvalue on one branch and lvalue on another must error.
# The lvalue_reassigned tracking prevents moving from potentially borrowed storage.
from tpy import Int32, Own


class Point:
    x: Int32


def consume(p: Own[Point]) -> Int32:
    return p.x


def test(a: Point, cond: bool) -> Int32:
    p = Point()
    p.x = 10
    if cond:
        p = Point()  # rvalue reassignment
        p.x = 20
    else:
        p = a  # lvalue reassignment -> borrowed
    return consume(p)  # tpyc: error(/implicit copy/)
