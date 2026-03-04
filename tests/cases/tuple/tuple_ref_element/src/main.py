# Tuple literal with reference type element -- reference semantics
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(x=" + str(self.x) + ", y=" + str(self.y) + ")"

def main() -> None:
    p = Point(Int32(1), Int32(2))
    t = (Int32(0), p)  # tpyc: ok
    print(t[0])
    print(t[1])
    # Mutation through reference is visible
    p.x = Int32(99)
    print(t[1])

main()
