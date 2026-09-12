# An escaping (`Callable`) closure snapshots its captures by value, so a frame
# member C++ cannot copy has no entry. `Own[Guard]` is the shape that needs the
# wrapper peeled first: `Own[T]` itself is copyable, and it is the PAYLOAD --
# a record with `__del__`, whose copy constructor C++ deletes -- that decides.
# Without the peel this renders an ill-formed copy the toolchain catches.
from typing import Callable, Iterator
from tpy import int32, Own


class Guard:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __del__(self) -> None:
        print("bye")


def apply(f: Callable[[int32], int32], v: int32) -> int32:
    return f(v)


def gen(g: Own[Guard]) -> Iterator[int32]:  # tpyc: error(/expr\.lambda/)
    yield apply(lambda i: i + g.x, 1)
    yield apply(lambda i: i + g.x, 2)


def main() -> None:
    for v in gen(Guard(5)):
        print(v)


main()
