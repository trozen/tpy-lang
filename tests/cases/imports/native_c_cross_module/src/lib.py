# Module defining a @native(binding="C") type and a helper function
from tpy.extern import native
from tpy import Int32, Ptr

@native("c_rect", binding="C")
class Rect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32

@native(binding="C")
def rect_area(r: Ptr[Rect]) -> Int32: ...
