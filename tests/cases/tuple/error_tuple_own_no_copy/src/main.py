# Own[T] element in return tuple requires explicit copy()
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
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
