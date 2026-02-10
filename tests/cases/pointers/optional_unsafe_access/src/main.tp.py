from tpy import Int32


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

    def mag(self) -> Int32:
        return self.x + self.y


# Accessing fields/methods on Optional without None check.
# Currently compiles without error (unsafe — UB if actually None at runtime).
# TODO: add compile-time narrowing analysis or runtime null checks.
def use_without_check(p: Point | None) -> Int32:
    return p.mag()  # tpyc: ok


points: list[Point] = list()
points.append(Point(3, 4))

def find(pts: list[Point], target: Int32) -> Point | None:
    for pt in pts:
        if pt.x == target:
            return pt
    return None

# Safe at runtime because we know the value exists
print(use_without_check(find(points, 3)))
