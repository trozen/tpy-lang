# Binding a tuple with a durable reference member (a param) to a local then
# yielding it by name silently copies the member; rejected (escape: yield directly).
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(b: Box) -> Iterator[tuple[Int32, Box]]:
    t = (1, b)
    yield t  # tpyc: error(/cannot yet alias it across a yield/)


def main() -> None:
    shared = Box(Int32(5))
    for pair in gen(shared):
        print(pair[1].val)


main()
