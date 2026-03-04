# Tuple literal with reference element passed directly as function argument.
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def show(pair: tuple[Point, Int32]) -> None:
    print(pair[0].x, pair[0].y, pair[1])

def main() -> None:
    p = Point(Int32(10), Int32(20))
    show((p, Int32(42)))
    # Mutation visible through reference
    p.x = Int32(99)
    show((p, Int32(7)))

main()
