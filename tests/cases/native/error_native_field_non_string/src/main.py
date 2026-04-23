# native_field() argument must be a string literal.
from tpy.extern import native, native_field
from tpy import Int32

@native
class Vec2:
    x: Int32 = native_field(123)  # tpyc: error(/argument must be a string literal/)
