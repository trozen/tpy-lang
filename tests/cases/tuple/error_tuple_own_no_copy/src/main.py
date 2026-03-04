# Own[T] element in return tuple requires explicit copy()
from tpy import Int32, Own

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def split(p: Point) -> tuple[Point, Own[Point]]:
    return (p, p)  # tpyc: error(/explicit copy/)

def main() -> None:
    pass

main()
