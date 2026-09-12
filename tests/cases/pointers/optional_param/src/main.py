from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

    def mag(self) -> int32:
        return self.x + self.y


def describe(p: Point | None) -> int32:
    if p is not None:
        return p.mag()
    return -1


def find(points: list[Point], target: int32) -> Point | None:
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
print(find(points, 5).x)      # tpyc: warning(/Potential None access on optional value/)
print(find(points, 5).mag())  # tpyc: warning(/Potential None access on optional value/)
