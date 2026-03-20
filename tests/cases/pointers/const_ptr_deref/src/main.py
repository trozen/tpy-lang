from tpy import Ptr, Int32, readonly, take_ptr

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    x: Int32 = 42
    cp: Ptr[readonly[Int32]] = take_ptr(x)
    print(cp.__deref__())

    pt: Point = Point(10, 20)
    cpp: Ptr[readonly[Point]] = take_ptr(pt)
    print(cpp.__deref__().x)
    print(cpp.__deref__().y)
    # Field access through Ptr[readonly[Point]] auto-deref
    print(cpp.x)
    print(cpp.y)

main()
