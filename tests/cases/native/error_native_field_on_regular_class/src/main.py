# native_field() is only allowed on @native classes; non-native use is rejected.
from tpy.extern import native_field
from tpy import int32

class Vec2:
    x: int32 = native_field("m_x")  # tpyc: error(/only allowed on @native classes/)
    y: int32 = int32(0)
