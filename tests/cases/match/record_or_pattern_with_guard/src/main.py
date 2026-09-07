# A record arm whose or-alternatives carry a GUARD: the guard composes into
# the one block condition instead of nesting a second if. Concretely,
# `case Point(x=0, y=0) | Point(x=1, y=1) if ok`, whose body mutates the
# caller's record so the write is visible after the call returns.
from dataclasses import dataclass
from tpy import Int32


@dataclass
class Point:
    x: Int32
    y: Int32


def f(p: Point, ok: bool) -> Int32:
    match p:
        # Both alternatives share one condition, and-ed with the guard.
        case Point(x=0, y=0) | Point(x=1, y=1) if ok:
            p.x = 9
            return 1
        case _:
            return 0


def main() -> None:
    pt = Point(1, 1)
    print(f(pt, True))
    # The subject is the caller's record, so the arm's write is visible.
    print(pt.x)
    print(f(Point(1, 1), False))
    print(f(Point(2, 2), True))


main()
