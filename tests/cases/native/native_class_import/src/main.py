from tpy.extern import native
from tpy import int32, Ptr
from lib import Vec2, MyRect, rect_area

# Native function using imported type from lib
@native
def vec2_sum(v: Ptr[Vec2]) -> int32: ...

def main() -> None:
    v = Vec2(int32(3), int32(7))
    print(v.x)
    print(vec2_sum(v))
    print(v.sum())

    r = MyRect(int32(0), int32(0), int32(40), int32(30))
    print(r.w)
    print(rect_area(r))
    print(r.area())

main()
