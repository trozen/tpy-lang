from tpy import int32


class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


class Holder:
    value: Point | None

    def __init__(self) -> None:
        self.value = None


def describe(p: Point | None) -> None:
    if p is not None:
        print(p.x)
        print(p.y)
    else:
        print("empty")


h = Holder()
describe(h.value)

h.value = Point(5, 6)
describe(h.value)
