# Generic function returning tuple[str, T] -- Ref wraps T when non-value.
# Codegen uses val_or_ref_t<T> in the generic return, val_or_ref<Point>
# in the monomorphized map template. Value types get no wrapping.
from tpy import Int32, copy_iter

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

def label[T](tag: str, val: T) -> tuple[str, T]:
    return (tag, val)

def main() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4)]
    vals: list[Int32] = [Int32(10), Int32(20)]

    # Non-value T=Point -- warns about copy
    d = dict(map(lambda p: label(str(p.x), p), pts))  # tpyc: warning(/copies tuple\[str, Point\] elements/)
    for k in d:
        print(k, d[k])

    # Value T=Int32 -- no warning
    d2 = dict(map(lambda v: label(str(v), v), vals))  # tpyc: ok
    for k in d2:
        print(k, d2[k])

    # copy_iter silences the warning
    d3 = dict(copy_iter(map(lambda p: label(str(p.x), p), pts)))  # tpyc: ok
    for k in d3:
        print(k, d3[k])

main()
