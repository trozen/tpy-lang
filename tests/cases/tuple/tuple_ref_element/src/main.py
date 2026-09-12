# Tuple literal with reference type element -- reference semantics
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(x=" + str(self.x) + ", y=" + str(self.y) + ")"

def main() -> None:
    p = Point(int32(1), int32(2))
    t = (int32(0), p)  # tpyc: ok
    print(t[0])
    print(t[1])
    # Mutation through reference is visible
    p.x = int32(99)
    print(t[1])

main()
