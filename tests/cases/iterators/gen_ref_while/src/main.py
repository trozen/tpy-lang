# Simple-while generator preserves references for non-value tuple elements,
# matching the simple-for-NativeIterable peephole. Mutation through the
# yielded tuple flows back to the iterable.
from tpy import Int32
from typing import Iterator


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def my_enumerate(items: list[Point]) -> Iterator[tuple[Int32, Point]]:
    i: Int32 = 0
    n = Int32(len(items))
    while i < n:
        yield (i, items[i])
        i += 1


def main() -> None:
    points = [Point(1, 2), Point(3, 4)]
    for i, p in my_enumerate(points):
        p.x = (i + 1) * 10
    for p in points:
        print(p.x, p.y)


main()
