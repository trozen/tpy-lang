from tpy import int32, copy


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


def find(items: list[Point], target: int32) -> Point | None:
    for p in items:
        if p.x == target:
            return p
    return None


pts: list[Point] = list()
pts.append(Point(1, 10))
pts.append(Point(2, 20))

h = Holder()
h.value = copy(find(pts, 2))
print(h.value is None)
print(h.value.x)
print(h.value.y)

h.value = copy(find(pts, 99))
print(h.value is None)
