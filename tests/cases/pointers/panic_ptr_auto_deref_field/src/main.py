from tpy import Ptr, Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p: Ptr[Point] = Ptr[Point]()
    print(p.x)  # tpyc: nullable(p)

main()
