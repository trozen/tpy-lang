from tpy import Ptr, Int32, take_ptr

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    x: Int32 = 42
    p: Ptr[Int32] = take_ptr(x)
    print(p.__deref__())

    pt: Point = Point(10, 20)
    pp: Ptr[Point] = take_ptr(pt)
    print(pp.__deref__().x)
    print(pp.__deref__().y)

main()
