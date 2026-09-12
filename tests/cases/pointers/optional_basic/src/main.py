from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


def test() -> None:
    p: Point | None = None
    print(p is None)
    p = Point(1, 2)
    print(p is None)
    print(p is not None)
    print(p.x)


test()
