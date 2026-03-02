# Tuple type annotation with reference type (no Own[]) should be rejected
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def make() -> tuple[Int32, Point]:  # tpyc: error(/reference type.*Own/)
    return (Int32(1), Point(Int32(1), Int32(2)))

def main() -> None:
    pass

main()
