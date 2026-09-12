from tpy.extern import native
from tpy import int32, Ptr

# @native(binding="C") class -- C struct import (aggregate init syntax)
@native(binding="C")
class Point:
    x: int32
    y: int32
    def manhattan(self) -> int32: ...

# @native(binding="C") with rename -- C name differs from Python name
@native("Rect", binding="C")
class MyRect:
    x: int32
    y: int32
    w: int32
    h: int32
    def area(self) -> int32: ...

# Native C functions that use the imported types
@native(binding="C")
def point_sum(p: Ptr[Point]) -> int32: ...

@native(binding="C")
def rect_area(r: Ptr[MyRect]) -> int32: ...

def main() -> None:
    p = Point(int32(10), int32(20))
    print(p.x)
    print(p.y)
    print(point_sum(p))
    print(p.manhattan())

    r = MyRect(int32(0), int32(0), int32(800), int32(600))
    print(r.w)
    print(rect_area(r))
    print(r.area())

main()
