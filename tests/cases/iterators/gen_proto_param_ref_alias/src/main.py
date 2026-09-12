# A generator over a protocol-typed iterable, forced onto the resumable frame
# by a second yield, must ALIAS its loop element rather than copy it into the
# frame: mutating through the loop var has to reach the caller's container.
# The value-element generator below is the inverse -- it must keep copy storage,
# so rebinding its loop var leaves the source list untouched.
from tpy import int32
from typing import Iterable, Iterator


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def bump(items: Iterable[Point]) -> Iterator[int32]:
    for p in items:
        p.x += 100
        yield p.x
        yield p.x


def doubled(nums: Iterable[int32]) -> Iterator[int32]:
    for n in nums:
        n += 1
        yield n
        yield n * 2


def main() -> None:
    pts = [Point(1), Point(2)]
    for v in bump(pts):
        print(v)
    print("mutations reached the caller:", pts[0].x, pts[1].x)

    ns = [10, 20]
    for v in doubled(ns):
        print(v)
    print("value elements untouched:", ns[0], ns[1])


main()
