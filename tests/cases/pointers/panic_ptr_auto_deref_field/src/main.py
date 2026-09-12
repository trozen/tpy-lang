from tpy import Ptr, int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p: Ptr[Point] = Ptr[Point]()
    print(p.x)  # tpyc: nullable(p)

main()
