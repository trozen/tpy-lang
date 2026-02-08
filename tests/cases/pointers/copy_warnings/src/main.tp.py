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

def main() -> None:
    r: Rect = Rect()
    p: Point = Point()
    r.corner = p                  # tpyc: warning(/copies Point into field/)
    r.corner = copy(p)            # tpyc: ok
    r.corner = Point()            # tpyc: ok
    r.width = 10                  # tpyc: ok
    print(r.width)

main()
