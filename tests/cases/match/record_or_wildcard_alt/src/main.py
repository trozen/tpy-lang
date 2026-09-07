# A record arm whose or-alternatives include a bare wildcard: the arm always
# matches, so it carries no field conditions at all. The non-exhaustive
# warning it still draws is a filed defect
# (BUGS.md#match-exhaustiveness-or-wildcard); this case pins it.
from dataclasses import dataclass
from tpy import Int32


@dataclass
class Point:
    x: Int32
    y: Int32


def f(p: Point) -> Int32:
    match p:  # tpyc: warning(/non\-exhaustive\ match\ on\ 'Point';\ no\ unconditional/)
        case Point(x=1, y=0):
            return 5
        # The wildcard alternative makes the whole arm unconditional.
        case Point(x=0, y=0) | _:
            p.y = 7
            return 1
    return 9


def main() -> None:
    print(f(Point(1, 0)))
    pt = Point(2, 3)
    print(f(pt))
    print(pt.y)


main()
