from tpy import Ptr, int32, take_ptr

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    x: int32 = 42
    p: Ptr[int32] = take_ptr(x)
    print(p.__deref__())

    pt: Point = Point(10, 20)
    pp: Ptr[Point] = take_ptr(pt)
    print(pp.__deref__().x)
    print(pp.__deref__().y)

main()
