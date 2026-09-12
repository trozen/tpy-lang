# Multi-iterable map with mixed Ref and Own params.
# fn takes (Point, Own[Point]): first arg by ref, second by value (moved).
from tpy import int32, Own

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def combine(p: Point, q: Own[Point]) -> Point:
    p.x = p.x + q.x
    return p

def main() -> None:
    pts = [Point(1, 2), Point(3, 4)]
    extras = [Point(10, 0), Point(20, 0)]
    for p in map(combine, pts, extras):
        pass
    print(pts[0].x)  # 11 (1 + 10, modified through reference)
    print(pts[1].x)  # 23 (3 + 20)

main()
