# A self-assignment (t = t) must PRESERVE the hazard fact: the fact is derived
# from the init before the target's old fact is cleared, so the redundant
# rebind does not silently drop the rejection.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(b: Box) -> Iterator[tuple[Int32, Box]]:
    t = (1, b)
    t = t
    yield t  # tpyc: error(/cannot yet alias it across a yield/)


def main() -> None:
    shared = Box(Int32(5))
    for pair in gen(shared):
        print(pair[1].val)


main()
