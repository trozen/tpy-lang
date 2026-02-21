from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


def find(points: list[Point], target: Int32) -> Point | None:
    for p in points:
        if p.x == target:
            return p
    return None


points: list[Point] = list()
points.append(Point(1, 10))
points.append(Point(2, 20))
points.append(Point(3, 30))
result = find(points, 2)
if result is not None:
    print(result.y)
result = find(points, 99)
print(result is None)
