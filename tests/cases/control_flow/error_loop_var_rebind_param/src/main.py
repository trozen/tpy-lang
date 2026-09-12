# Rebinding a reference-type PARAM via a for-loop is rejected (a hoisted
# assignment would write through the reference into the caller's object).
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(p: Point, items: list[Point]) -> None:
    for p in items:  # tpyc: error(/for-loop rebind of reference-type variable 'p'/)
        print(p.x)


def main() -> None:
    f(Point(0), [Point(1)])


main()
