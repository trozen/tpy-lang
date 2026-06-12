# Optional subject with only a None arm: the missing value side must be
# reported (an only-None match is NOT exhaustive).
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def f(p: Point | None) -> Int32:
    match p:  # tpyc: warning(/non-exhaustive match.*missing: Point/)
        case None:
            return -1
    return 0


def main() -> None:
    print(f(None))
    print(f(Point(3)))


main()
