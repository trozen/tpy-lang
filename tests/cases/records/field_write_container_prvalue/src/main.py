# A container field written from a value that materializes its own owned
# container -- a `[e] * n` repeat and a container-returning method-call
# rvalue. Both assign bare; the field owns the result, so mutating through
# it afterwards must be visible.
from __future__ import annotations
from tpy import int32


class Canvas:
    pixels: list[int32]
    lines: list[str]
    tags: set[int32]

    def __init__(self) -> None:
        self.pixels = []
        self.lines = []
        self.tags = set()

    def fill(self, n: int32, v: int32) -> None:
        self.pixels = [v] * n  # tpyc: ok -- the repeat materializes the list

    def slurp(self, data: str) -> None:
        self.lines = data.splitlines()  # tpyc: ok -- a method-call rvalue

    def merge(self, a: set[int32], b: set[int32]) -> None:
        self.tags = a.union(b)  # tpyc: ok -- the same row on a set field


def main() -> None:
    c = Canvas()
    c.fill(4, 7)
    c.pixels[0] = 9
    print(len(c.pixels), c.pixels[0], c.pixels[3])
    c.slurp("alpha\nbeta\ngamma")
    c.lines.append("delta")
    print(len(c.lines), c.lines[1], c.lines[3])
    x: set[int32] = {1, 2}
    y: set[int32] = {2, 3}
    c.merge(x, y)
    c.tags.add(9)
    print(len(c.tags), len(x), 9 in c.tags)
    # A second write replaces the field's storage outright.
    c.fill(2, 1)
    print(len(c.pixels), c.pixels[1])


main()
