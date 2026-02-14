from tpy import ConstPtr, Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    x: Int32 = 42
    cp: ConstPtr[Int32] = ConstPtr(x)
    print(cp.__deref__())

    pt: Point = Point(10, 20)
    cpp: ConstPtr[Point] = ConstPtr(pt)
    print(cpp.__deref__().x)
    print(cpp.__deref__().y)
    # Field access through ConstPtr auto-deref
    print(cpp.x)
    print(cpp.y)

main()
