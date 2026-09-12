# map() distinguishes reference vs owned returns on the same data.
# identity -> Point returns by reference (val_or_ref, mutations propagate).
# make_new -> Own[Point] returns by value (bare Point, independent copy).
# set_x with multi-iterable: one yields refs (Point), one yields values (int32).
from tpy import int32, Own

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def identity(p: Point) -> Point:
    return p

def make_new(p: Point) -> Own[Point]:
    return Point(p.x * 2, p.y * 2)

def set_x(p: Point, new_x: int32) -> Point:
    p.x = new_x
    return p

def main() -> None:
    pts = [Point(1, 2), Point(3, 4)]

    # Ref return: mutations propagate to original
    for p in map(identity, pts):
        p.x = p.x + 100
    print(pts[0].x)  # 101
    print(pts[1].x)  # 103

    # Own return: independent copies, originals unchanged
    for p in map(make_new, pts):
        p.x = 999  # modifies copy only
    print(pts[0].x)  # 101 (unchanged)
    print(pts[1].x)  # 103 (unchanged)

    # Multi-iterable: ref Point + value int32
    vals = [10, 20]
    for p in map(set_x, pts, vals):
        pass
    print(pts[0].x)  # 10 (set through reference)
    print(pts[1].x)  # 20

main()
