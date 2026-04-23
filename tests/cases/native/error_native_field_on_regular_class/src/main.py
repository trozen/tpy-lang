# native_field() is only allowed on @native classes; non-native use is rejected.
from tpy.extern import native_field
from tpy import Int32

class Vec2:
    x: Int32 = native_field("m_x")  # tpyc: error(/only allowed on @native classes/)
    y: Int32 = Int32(0)
