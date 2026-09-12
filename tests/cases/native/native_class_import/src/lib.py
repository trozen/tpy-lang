from tpy.extern import native
from tpy import int32, Ptr

# C++ class with rename
@native("ns::Vec2")
class Vec2:
    x: int32
    y: int32
    def sum(self) -> int32: ...

# C struct with rename
@native("Rect", binding="C")
class MyRect:
    x: int32
    y: int32
    w: int32
    h: int32
    def area(self) -> int32: ...

# Native function using native type from this module
@native(binding="C")
def rect_area(r: Ptr[MyRect]) -> int32: ...
