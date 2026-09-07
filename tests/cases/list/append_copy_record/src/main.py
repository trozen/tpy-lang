# `append(copy(p))` copy-constructs into the element's Own[T] slot: mutating the
# source afterwards must leave the stored element alone.
from tpy import Int32, copy


class Point:
    x: Int32

    def __init__(self) -> None:
        self.x = 0


def stash(items: list[Point], p: Point) -> None:
    items.append(copy(p))  # the copy-construct rvalue binding the Own[T] slot


def main() -> None:
    p = Point()
    items: list[Point] = []
    stash(items, p)
    p.x = 9
    print(len(items), items[0].x, p.x)


main()
