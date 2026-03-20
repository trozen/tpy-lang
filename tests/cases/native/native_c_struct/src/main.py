from tpy.extern import native_c
from tpy import Int32, Ptr, take_ptr

# @native_c class — C struct import (aggregate init syntax)
@native_c
class Point:
    x: Int32
    y: Int32
    def manhattan(self) -> Int32: ...

# @native_c with rename — C name differs from Python name
@native_c("Rect")
class MyRect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32
    def area(self) -> Int32: ...

# Native C functions that use the imported types
@native_c
def point_sum(p: Ptr[Point]) -> Int32: ...

@native_c
def rect_area(r: Ptr[MyRect]) -> Int32: ...

def main() -> None:
    p = Point(Int32(10), Int32(20))
    print(p.x)
    print(p.y)
    print(point_sum(take_ptr(p)))
    print(p.manhattan())

    r = MyRect(Int32(0), Int32(0), Int32(800), Int32(600))
    print(r.w)
    print(rect_area(take_ptr(r)))
    print(r.area())

main()
