from tpy import native, native_c, Int32, Ptr

# @native class — C++ class import (constructor call syntax)
@native
class Vec2:
    x: Int32
    y: Int32

# @native with rename — fully qualified C++ name
@native("ns::Color")
class Color:
    r: Int32
    g: Int32
    b: Int32

# Native C++ functions that use the imported types
@native
def vec2_sum(v: Ptr[Vec2]) -> Int32: ...

@native
def color_brightness(c: Ptr[Color]) -> Int32: ...

def main() -> None:
    v = Vec2(Int32(3), Int32(4))
    print(v.x)
    print(v.y)
    print(vec2_sum(Ptr(v)))

    c = Color(Int32(100), Int32(150), Int32(200))
    print(c.r)
    print(color_brightness(Ptr(c)))

main()
