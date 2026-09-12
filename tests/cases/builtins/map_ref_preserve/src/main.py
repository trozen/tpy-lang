# map() preserves reference semantics for non-value return types.
# When the mapped function returns by reference (not Own), map yields
# val_or_ref<T> so mutations propagate to the original container elements.
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def identity(p: Point) -> Point:
    return p

def scale(p: Point, factor: int32) -> Point:
    p.x = p.x * factor
    p.y = p.y * factor
    return p

def main() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4), Point(5, 6)]

    # identity returns by reference -- modifications go through
    for p in map(identity, pts):
        p.x = p.x * 10
    print(pts[0].x)  # 10 (modified through reference)
    print(pts[1].x)  # 30
    print(pts[2].x)  # 50

    # scale mutates and returns by reference
    for p in map(lambda p: scale(p, 2), pts):
        pass  # scale already mutated via reference param
    print(pts[0].x)  # 20 (10 * 2)
    print(pts[1].x)  # 60 (30 * 2)

main()
