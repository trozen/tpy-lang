# native_field() rejects more than one positional argument.
from tpy.extern import native, native_field
from tpy import Int32

@native
class Vec2:
    x: Int32 = native_field("m_x", "extra")  # tpyc: error(/takes exactly 1 positional string argument/)
