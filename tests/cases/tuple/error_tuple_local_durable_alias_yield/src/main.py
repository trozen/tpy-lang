# The durable hazard fact propagates through a local-to-local alias: binding a
# tuple with a durable reference member, aliasing it (u = t), then yielding the
# alias by name silently copies the member -- rejected like the direct form.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(b: Box) -> Iterator[tuple[Int32, Box]]:
    t = (1, b)
    u = t
    yield u  # tpyc: error(/cannot yet alias it across a yield/)


def main() -> None:
    shared = Box(Int32(5))
    for pair in gen(shared):
        print(pair[1].val)


main()
