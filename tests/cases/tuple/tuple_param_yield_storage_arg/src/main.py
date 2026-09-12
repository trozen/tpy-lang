# A storage-form tuple arg into a yield-escaping generator: the yield hands
# out mutable element pointers, so the param verdict must stay non-const and
# the call-site lift must match the factory spelling. The caller's mutation
# through the yielded element is observed in the source field (CPython
# aliasing).
from typing import Iterator
from tpy import int32, Own


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, b: Own[Box]) -> None:
        self.pair = (1, b)


def gen(p: tuple[int32, Box]) -> Iterator[tuple[int32, Box]]:
    yield p


def main() -> None:
    h = Holder(Box(5))
    for pair in gen(h.pair):
        pair[1].val = 99
    print(h.pair[1].val)


main()
