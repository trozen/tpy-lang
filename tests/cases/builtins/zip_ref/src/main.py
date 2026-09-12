# zip() preserves references: mutations through loop var modify original
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    points = [Point(1, 2), Point(3, 4)]
    scales = [10, 20]
    for p, s in zip(points, scales):
        p.x *= s
        p.y *= s
    for p in points:
        print(p.x, p.y)

main()
