# native_field() -- per-field C++ rename on @native classes.
# The generated C++ must reference m_x / m_y, not x / y, or the link fails.
from tpy.extern import native, native_field
from tpy import int32

@native
class Vec2:
    x: int32 = native_field("m_x")
    y: int32 = native_field("m_y")
    def sum(self) -> int32: ...

def main() -> None:
    v = Vec2(int32(3), int32(4))
    print(v.x)
    print(v.y)
    v.x = int32(10)
    print(v.x)
    print(v.sum())

main()
