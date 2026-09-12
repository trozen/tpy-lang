# A record-returning method reached through a Ptr deref is admitted only in
# RECEIVER position; the same call binding a local stays out.
from tpy import int32, Ptr, copy, take_ptr


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


class Cell:
    p: Point

    def __init__(self, p: Point) -> None:
        self.p = copy(p)

    def get_point(self) -> Point:
        return self.p


def use(c: Ptr[Cell]) -> None:
    if c is not None:
        q = c.get_point()  # tpyc: error(/method.qualcall.ret.record_f1/)
        print(q.x)


def main() -> None:
    cell = Cell(Point(1, 2))
    use(take_ptr(cell))


main()
