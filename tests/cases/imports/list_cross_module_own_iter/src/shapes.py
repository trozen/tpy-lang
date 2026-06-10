# A reference-type record + a generator yielding Iterator[Own[Point]], for the
# cross-module list()-materialization qualification regression.
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
