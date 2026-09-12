# A reference-type record + a generator yielding Iterator[Own[Point]], for the
# cross-module list()-materialization qualification regression.
from tpy import int32, Own, copy
from typing import Iterator

class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

def points() -> Iterator[Own[Point]]:
    src: list[Point] = [Point(3), Point(1), Point(2)]
    for p in src:
        yield copy(p)
