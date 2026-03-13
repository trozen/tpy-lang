# Test: ref-returning call passed directly as mutable ref arg -- no copy, mutation propagates


class Point:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Holder:
    inner: Point

    def __init__(self) -> None:
        self.inner = Point(5)

    def get(self) -> Point:
        return self.inner


def find_first(items: list[Point]) -> Point:
    return items[0]


def bump(p: Point) -> None:
    p.x += 10


def test() -> None:
    pts = [Point(1), Point(2)]

    # Free function: find_first returns Point& -> bump receives it directly
    bump(find_first(pts))
    print(pts[0].x)   # 11

    # Method: holder.get() returns Point& -> bump receives it directly
    h = Holder()
    bump(h.get())
    print(h.inner.x)  # 15


test()
