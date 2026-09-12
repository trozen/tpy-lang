from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

    def mag(self) -> int32:
        return self.x + self.y


# Accessing fields/methods on Optional without None check.
# Compiles with warning and inserts a runtime null check.
def use_without_check(p: Point | None) -> int32:
    return p.mag()  # tpyc: warning(/Potential None access on optional value/)


points: list[Point] = list()
points.append(Point(3, 4))

def find(pts: list[Point], target: int32) -> Point | None:
    for pt in pts:
        if pt.x == target:
            return pt
    return None

# Safe at runtime because we know the value exists
print(use_without_check(find(points, 3)))
