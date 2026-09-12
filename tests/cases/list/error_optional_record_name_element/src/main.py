# A record NAME at a `list[Record | None]` element slot: the optional inner would
# need the pointer-local deref plus last-use move threaded through it.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def wrap(p: Point) -> int32:
    xs: list[Point | None] = [p]  # tpyc: error(/container_literal/)
    return len(xs)


def main() -> None:
    print(wrap(Point(1)))


main()
