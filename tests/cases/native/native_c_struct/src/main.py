from tpy.extern import native
from tpy import Int32, Ptr

# @native(binding="C") class -- C struct import (aggregate init syntax)
@native(binding="C")
class Point:
    x: Int32
    y: Int32
    def manhattan(self) -> Int32: ...

# @native(binding="C") with rename -- C name differs from Python name
@native("Rect", binding="C")
class MyRect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32
    def area(self) -> Int32: ...

# Native C functions that use the imported types
@native(binding="C")
def point_sum(p: Ptr[Point]) -> Int32: ...

@native(binding="C")
def rect_area(r: Ptr[MyRect]) -> Int32: ...

def main() -> None:
    p = Point(Int32(10), Int32(20))
    print(p.x)
    print(p.y)
    print(point_sum(p))
    print(p.manhattan())

    r = MyRect(Int32(0), Int32(0), Int32(800), Int32(600))
    print(r.w)
    print(rect_area(r))
    print(r.area())

main()
