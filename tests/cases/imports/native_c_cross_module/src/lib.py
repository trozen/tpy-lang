# Module defining a @native_c type and a helper function
from tpy.extern import native_c
from tpy import Int32, Ptr

@native_c("c_rect")
class Rect:
    x: Int32
    y: Int32
    w: Int32
    h: Int32

@native_c
def rect_area(r: Ptr[Rect]) -> Int32: ...
