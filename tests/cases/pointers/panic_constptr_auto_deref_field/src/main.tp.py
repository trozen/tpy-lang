from tpy import ConstPtr, Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p: ConstPtr[Point] = ConstPtr[Point]()
    print(p.x)

main()
