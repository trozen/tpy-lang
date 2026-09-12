from tpy import int32, copy


class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y


class Edge:
    target: Point | None

    def __init__(self, p: Point | None):
        self.target = copy(p)


def find(items: list[Point], target: int32) -> Point | None:
    for p in items:
        if p.x == target:
            return p
    return None


pts: list[Point] = list()
pts.append(Point(5, 50))
pts.append(Point(3, 30))

e1 = Edge(None)
print(e1.target is None)

p = Point(7, 70)
e2 = Edge(p)
print(e2.target is not None)
print(e2.target.x)
print(e2.target.y)

e3 = Edge(find(pts, 3))
print(e3.target.x)

e4 = Edge(find(pts, 99))
print(e4.target is None)
