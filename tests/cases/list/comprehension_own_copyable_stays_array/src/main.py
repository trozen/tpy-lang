# A comprehension whose element returns Own[CopyableRecord]: the Own collapses
# to storage form, but a default-constructible copyable element keeps the stack
# Array (exercises the Own-collapse independently of the nocopy fallback).
from tpy import int32, Own


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def make(i: int32) -> Own[Point]:
    return Point(i)


def main() -> None:
    pts = [make(i) for i in range(4)]  # tpyc: type(/Array\[Point, 4\]/)
    total = 0
    for p in pts:
        total += p.x
    print(total)


main()
