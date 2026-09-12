# native_field() argument must be a string literal.
from tpy.extern import native, native_field
from tpy import int32

@native
class Vec2:
    x: int32 = native_field(123)  # tpyc: error(/argument must be a string literal/)
