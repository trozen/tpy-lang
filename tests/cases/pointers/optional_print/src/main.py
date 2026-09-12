from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

    def __repr__(self) -> str:
        return f"Point(x={self.x}, y={self.y})"


def find(points: list[Point], target: int32) -> Point | None:
    for p in points:
        if p.x == target:
            return p
    return None


points: list[Point] = list()
points.append(Point(3, 4))

# Print Optional from function return
print(find(points, 3))
print(find(points, 99))

# Print None literal
print(None)

# Print Optional local
p: Point | None = None
print(p)
p = Point(1, 2)
print(p)
