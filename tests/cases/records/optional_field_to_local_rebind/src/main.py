from tpy import Int32, copy


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


class Holder:
    value: Point | None

    def __init__(self) -> None:
        self.value = None


def test(h: Holder) -> None:
    p: Point | None = h.value
    print(p is None)

    h.value = copy(Point(1, 2))
    p = h.value
    print(p is None)
    print(p.x)

    p = None
    print(p is None)

    h.value = copy(Point(3, 4))
    p = h.value
    print(p.y)


h = Holder()
test(h)
