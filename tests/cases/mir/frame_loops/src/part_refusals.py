# Decomposed loops MIR does not lower yet, each refused by name.
from __future__ import annotations

from typing import Iterator, Self

from tpy import Own, int32

LIMITS: list[int32] = [7, 8]


# widened_target: a uint8 element bound to an int32 variable: no conversion is modeled
def bumped_bytes(d: bytes) -> Iterator[int32]:  # tpyc: mir(uncovered /unsupported native target binding/)
    for c in d:
        c += 1
        yield c


# range_step: a third `range` bound; its step is not published with the loop
def stepped(n: int32) -> Iterator[int32]:  # tpyc: mir(uncovered /range step across a suspension/)
    for i in range(0, n, 2):
        yield i


# tuple_head: a head unpack
def sums(pairs: list[tuple[int32, int32]]) -> Iterator[int32]:  # tpyc: mir(uncovered /unsupported native container element/)
    for a, b in pairs:
        yield a + b


# frame_list: a list the frame owns
def owned() -> Iterator[int32]:  # tpyc: mir(uncovered /unsupported hoisted value/)
    ys = [1, 2]
    for y in ys:
        yield y


# global_source: a frame publishes no global binding
def limits() -> Iterator[int32]:  # tpyc: mir(uncovered /container source needs a place/)
    for x in LIMITS:
        yield x


class Pile:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    # owned_receiver: a consuming method's frame owns its receiver, as an owned-value capture its parameter
    def drain(self: Own[Self]) -> Iterator[int32]:  # tpyc: mir(uncovered /unsupported owned_value capture/)
        yield len(self.items)


def main() -> None:
    print("widened_target:", list(bumped_bytes(b"ab")))
    print("range_step:", list(stepped(5)))
    print("tuple_head:", list(sums([(1, 2), (3, 4)])))
    print("frame_list:", list(owned()))
    print("global_source:", list(limits()))
    pile = Pile()
    print("owned_receiver:", len(pile.items))
