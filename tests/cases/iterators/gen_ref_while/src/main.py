# While-loop generator preserves references for non-value tuple elements,
# matching the for-over-container twin (gen_ref). Mutation through the
# yielded tuple flows back to the iterable.
from tpy import int32
from typing import Iterator


class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


def my_enumerate(items: list[Point]) -> Iterator[tuple[int32, Point]]:
    i: int32 = 0
    n = int32(len(items))
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
