# Own[T] element in return tuple requires explicit copy()
from tpy import int32, Own

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def split(p: Point) -> tuple[Point, Own[Point]]:
    return (p, p)  # tpyc: error(/explicit copy/)

def main() -> None:
    pass

main()
