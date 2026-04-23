# native_field() -- per-field C++ rename on @native classes.
# The generated C++ must reference m_x / m_y, not x / y, or the link fails.
from tpy.extern import native, native_field
from tpy import Int32

@native
class Vec2:
    x: Int32 = native_field("m_x")
    y: Int32 = native_field("m_y")
    def sum(self) -> Int32: ...

def main() -> None:
    v = Vec2(Int32(3), Int32(4))
    print(v.x)
    print(v.y)
    v.x = Int32(10)
    print(v.x)
    print(v.sum())

main()
