# 8b: return_borrows_from -- track which params a returned reference borrows from.
# Returned refs register the result as borrowing the source argument at call sites.
from tpy import int32, Own


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


# Direct param subscript -- borrows from items (param 0)
def get_first(items: list[Point]) -> Point:
    return items[0]  # tpyc: ok


# Direct param -- borrows from p (param 0)
def identity(p: Point) -> Point:
    return p  # tpyc: ok


# No borrow: returns a newly constructed value
def make_point(x: int32) -> Own[Point]:
    return Point(x, x)  # tpyc: ok


# Method returning from self (index -1)
class Container:
    _items: list[Point]

    def __init__(self) -> None:
        self._items = []

    def add(self, p: Point) -> None:
        self._items.append(p)

    def first(self) -> Point:
        return self._items[0]  # tpyc: ok (borrows from self)


def main() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4)]

    # f borrows from pts (return_borrows_from = {0})
    f = get_first(pts)
    print(f.x)

    # q borrows from p (return_borrows_from = {0})
    p = Point(10, 20)
    q = identity(p)
    print(q.x)

    # r owns its own storage (return_borrows_from = frozenset())
    r = make_point(int32(5))
    print(r.x)

    # first borrows from c (return_borrows_from = {-1})
    c = Container()
    c.add(Point(7, 8))
    first = c.first()
    print(first.x)


main()
