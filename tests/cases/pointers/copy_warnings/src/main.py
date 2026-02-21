from tpy import Int32, copy

class Point:
    x: Int32
    y: Int32

class Rect:
    corner: Point
    width: Int32

    def set_corner(self, p: Point) -> None:
        self.corner = p           # tpyc: warning(/copies Point into field/)
        self.corner = copy(p)     # tpyc: ok
        self.corner = Point()     # tpyc: ok

    def set_width(self, w: Int32) -> None:
        self.width = w            # tpyc: ok

class Container:
    items: list[Int32]

    def set_items(self, data: list[Int32]) -> None:
        self.items = data         # tpyc: warning(/copies list\[Int32\] into field/)
        self.items = copy(data)   # tpyc: ok
        self.items = [1, 2, 3]    # tpyc: ok

class Holder[T]:
    value: T

    def set_value(self, v: T) -> None:
        self.value = v            # tpyc: warning(/may copy T into field/)
        self.value = copy(v)      # tpyc: ok

class OptHolder:
    value: Point | None

    def __init__(self) -> None:
        self.value = None

    def set_value(self, p: Point | None) -> None:
        self.value = p            # tpyc: warning(/copies Point | None into field/)
        self.value = copy(p)      # tpyc: ok

def find_point(pts: list[Point], x: Int32) -> Point | None:
    for p in pts:
        if p.x == x:
            return p
    return None

def main() -> None:
    r: Rect = Rect()
    p: Point = Point()
    r.corner = p                  # tpyc: warning(/copies Point into field/)
    r.corner = copy(p)            # tpyc: ok
    r.corner = Point()            # tpyc: ok
    r.width = 10                  # tpyc: ok

    # Optional field: function returning T | None
    h: OptHolder = OptHolder()
    pts: list[Point] = list()
    h.value = find_point(pts, 1)  # tpyc: warning(/copies Point | None into field/)
    h.value = copy(find_point(pts, 1))  # tpyc: ok
    # Optional field: field-to-field (lvalue)
    h.value = h.value             # tpyc: warning(/copies Point | None into field/)
    h.value = copy(h.value)       # tpyc: ok
    print(r.width)

main()
