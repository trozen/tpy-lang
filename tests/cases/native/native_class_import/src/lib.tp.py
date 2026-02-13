from tpy import native, native_c, Int32, Ptr

# C++ class with rename
@native("ns::Vec2")
class Vec2:
    x: Int32
    y: Int32

# C struct with rename
@native_c("Rect")
class MyRect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32

# Native function using native type from this module
@native_c
def rect_area(r: Ptr[MyRect]) -> Int32: ...
