# The adjacent shape that must keep rejecting: a frame tuple unpack whose
# source chain has a CALL in it. A chain of field / subscript hops off a
# declared name lifts through `tuple_to_pointer` off the lvalue, but a call
# result is a temporary that dies at the end of the case block, so the frame's
# alias field would point past it at the next resume -- the reject is located
# rather than rendered. Same rule as the sync twins
# (BUGS.md#chain-temporary-hop-rejected): a hop must name storage the root
# outlives.
from typing import Iterator

from tpy import int32, Own


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, n: int32) -> None:
        self.pair = (n, Box(n * 2))


def make() -> Own[list[Holder]]:
    return [Holder(1)]


def gen() -> Iterator[int32]:
    a, b = make()[0].pair  # tpyc: error(/not yet supported by C\+\+ code generation \(res.unpack\)/)
    yield a
    yield b.n


def main() -> None:
    for v in gen():
        print(v)


main()
