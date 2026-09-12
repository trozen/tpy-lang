# native_field() requires exactly one argument.
from tpy.extern import native, native_field
from tpy import int32

@native
class Vec2:
    x: int32 = native_field()  # tpyc: error(/takes exactly 1 positional string argument/)
