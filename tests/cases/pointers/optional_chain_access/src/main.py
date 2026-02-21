from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

    def mag(self) -> Int32:
        return self.x + self.y


def find(points: list[Point], target: Int32) -> Point | None:
    for p in points:
        if p.x == target:
            return p
    return None


points: list[Point] = list()
points.append(Point(3, 4))
points.append(Point(5, 6))

# Chain field/method access on Optional-returning expression
print(find(points, 3).x)      # tpyc: warning(/Potential None access on optional value/)
print(find(points, 5).mag())  # tpyc: warning(/Potential None access on optional value/)
