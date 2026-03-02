# Tuple literal with reference type element (no Own[]) should be rejected
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    p = Point(Int32(1), Int32(2))
    t = (Int32(0), p)  # tpyc: error(/reference type.*Own/)

main()
