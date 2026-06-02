# The escape the dangle diagnostic points to: annotate the tuple element
# Own[Box]. The fresh Box then moves into the storage-form yield slot, so a
# bare-name yield of the owning local is valid (no borrow taken). Also confirms
# the owns-fresh flag does NOT fire when the element type is Own.
from typing import Iterator
from tpy import Int32, Own


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(n: Int32) -> Iterator[tuple[Int32, Own[Box]]]:
    i = Int32(0)
    while i < n:
        t = (i, Box(i * 10))
        yield t
        i += 1


def main() -> None:
    for pair in gen(Int32(3)):
        print(pair[0], pair[1].val)


main()
