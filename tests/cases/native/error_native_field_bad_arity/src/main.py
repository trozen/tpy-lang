# native_field() requires exactly one argument.
from tpy.extern import native, native_field
from tpy import Int32

@native
class Vec2:
    x: Int32 = native_field()  # tpyc: error(/takes exactly 1 positional string argument/)
