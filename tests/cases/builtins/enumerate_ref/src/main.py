# enumerate() preserves references: mutations through loop var modify original
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    points = [Point(1, 2), Point(3, 4), Point(5, 6)]
    for i, p in enumerate(points):
        p.x = (i + 1) * 10
    for p in points:
        print(p.x, p.y)

main()
