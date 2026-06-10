# Regression guard: list() of a generator yielding a SAME-MODULE Own[record].
# The cross-module cpp_template qualification must NOT over-qualify a
# same-module element -- it stays the bare short name (not namespace-prefixed).
from tpy import Int32, Own, copy
from typing import Iterator

class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x

def points() -> Iterator[Own[Point]]:
    src: list[Point] = [Point(3), Point(1), Point(2)]
    for p in src:
        yield copy(p)

def main() -> None:
    out = list(points())
    for p in out:
        print(p.x)

main()
