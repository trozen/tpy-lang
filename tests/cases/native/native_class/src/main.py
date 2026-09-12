from tpy.extern import native
from tpy import int32, Float, Ptr, Own

# @native class — C++ class import (constructor call syntax)
@native
class Vec2:
    x: int32
    y: int32
    def sum(self) -> int32: ...
    def dot(self, other: Vec2) -> int32: ...
    # C++ `static Vec2 zero()` returns a fresh value -> Own (a bare `-> Vec2`
    # would mark it a borrow and alias a destroyed temporary).
    @staticmethod
    def zero() -> Own[Vec2]: ...

# @native with rename — fully qualified C++ name
@native("ns::Color")
class Color:
    r: int32
    g: int32
    b: int32
    def brightness(self) -> int32: ...

# Native C++ functions that use the imported types
@native
def vec2_sum(v: Ptr[Vec2]) -> int32: ...

@native
def color_brightness(c: Ptr[Color]) -> int32: ...

def main() -> None:
    v = Vec2(int32(3), int32(4))
    print(v.x)
    print(v.y)
    print(vec2_sum(v))
    print(v.sum())
    print(v.dot(Vec2(int32(1), int32(2))))

    z = Vec2.zero()
    print(z.x)
    print(z.y)

    c = Color(int32(100), int32(150), int32(200))
    print(c.r)
    print(color_brightness(c))
    print(c.brightness())

main()
