# Module defining a @native(binding="C") type and a helper function
from tpy.extern import native
from tpy import int32, Ptr

@native("c_rect", binding="C")
class Rect:
    x: int32
    y: int32
    w: int32
    h: int32

@native(binding="C")
def rect_area(r: Ptr[Rect]) -> int32: ...
