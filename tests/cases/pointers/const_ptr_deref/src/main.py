from tpy import ReadOnlyPtr, Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    x: Int32 = 42
    cp: ReadOnlyPtr[Int32] = ReadOnlyPtr(x)
    print(cp.__deref__())

    pt: Point = Point(10, 20)
    cpp: ReadOnlyPtr[Point] = ReadOnlyPtr(pt)
    print(cpp.__deref__().x)
    print(cpp.__deref__().y)
    # Field access through ReadOnlyPtr auto-deref
    print(cpp.x)
    print(cpp.y)

main()
