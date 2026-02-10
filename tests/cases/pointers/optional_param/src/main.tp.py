from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

    def mag(self) -> Int32:
        return self.x + self.y


def describe(p: Point | None) -> Int32:
    if p is not None:
        return p.mag()
    return -1


def find(points: list[Point], target: Int32) -> Point | None:
    for p in points:
        if p.x == target:
            return p
    return None


points: list[Point] = list()
points.append(Point(3, 4))
points.append(Point(5, 6))

result = find(points, 3)
print(describe(result))
print(describe(None))
print(describe(find(points, 99)))

# Inline field/method access on Optional-returning expression
print(find(points, 5).x)
print(find(points, 5).mag())
