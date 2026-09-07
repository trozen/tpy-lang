# A record NAME at a `list[Record | None]` element slot: the optional inner would
# need the pointer-local deref plus last-use move threaded through it.
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def wrap(p: Point) -> Int32:
    xs: list[Point | None] = [p]  # tpyc: error(/container_literal/)
    return len(xs)


def main() -> None:
    print(wrap(Point(1)))


main()
