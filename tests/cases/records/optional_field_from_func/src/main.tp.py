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


def find(items: list[Point], target: Int32) -> Point | None:
    for p in items:
        if p.x == target:
            return p
    return None


pts: list[Point] = list()
pts.append(Point(1, 10))
pts.append(Point(2, 20))

h = Holder()
h.value = find(pts, 2)
print(h.value is None)
print(h.value.x)
print(h.value.y)

h.value = find(pts, 99)
print(h.value is None)
