# Locals bound from a non-dangling call return are themselves returnable,
# including via aliasing and through branches where every path is trusted.
from tpy import Int32, Ptr


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


g: Point = Point(1, 2)


def addr_global() -> Ptr[Point]:
    return g


def via_local() -> Ptr[Point]:
    p: Ptr[Point] = addr_global()  # tpyc: ok
    return p  # tpyc: ok


def via_alias() -> Ptr[Point]:
    p: Ptr[Point] = addr_global()
    q: Ptr[Point] = p  # tpyc: ok
    return q  # tpyc: ok


def via_both_branches(cond: bool) -> Ptr[Point]:
    if cond:
        p: Ptr[Point] = addr_global()
    else:
        p = addr_global()
    return p  # tpyc: ok


def via_ternary(cond: bool) -> Ptr[Point]:
    p: Ptr[Point] = addr_global() if cond else addr_global()  # tpyc: ok
    return p  # tpyc: ok


def main() -> None:
    a: Ptr[Point] = via_local()
    b: Ptr[Point] = via_alias()
    c: Ptr[Point] = via_both_branches(True)
    d: Ptr[Point] = via_ternary(False)
    print(a.x)  # tpyc: nullable(a)
    print(b.y)  # tpyc: nullable(b)
    print(c.x)  # tpyc: nullable(c)
    print(d.y)  # tpyc: nullable(d)


main()
