from tpy.extern import native
from tpy import Int32, Ptr
from lib import Vec2, MyRect, rect_area

# Native function using imported type from lib
@native
def vec2_sum(v: Ptr[Vec2]) -> Int32: ...

def main() -> None:
    v = Vec2(Int32(3), Int32(7))
    print(v.x)
    print(vec2_sum(v))
    print(v.sum())

    r = MyRect(Int32(0), Int32(0), Int32(40), Int32(30))
    print(r.w)
    print(rect_area(r))
    print(r.area())

main()
