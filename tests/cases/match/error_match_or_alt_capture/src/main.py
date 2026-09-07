# A field CAPTURE inside or-alternatives: the two alternatives bind the same
# name from different fields, which one block condition cannot express.
from dataclasses import dataclass
from tpy import Int32


@dataclass
class Point:
    x: Int32
    y: Int32


def f(p: Point) -> Int32:
    match p:  # tpyc: error(/stmt\.match/)
        # `a` comes from `x` in one alternative and from `y` in the other.
        case Point(x=a, y=0) | Point(x=0, y=a):
            return a
        case _:
            return 0


def main() -> None:
    print(f(Point(3, 0)))


main()
