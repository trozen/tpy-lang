# Tuple literal with reference element passed directly as function argument.
from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def show(pair: tuple[Point, int32]) -> None:
    print(pair[0].x, pair[0].y, pair[1])

def main() -> None:
    p = Point(int32(10), int32(20))
    show((p, int32(42)))
    # Mutation visible through reference
    p.x = int32(99)
    show((p, int32(7)))

main()
