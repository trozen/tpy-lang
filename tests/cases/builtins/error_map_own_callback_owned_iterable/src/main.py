# An `Own[Box]` callback over an `Iterable[Own[Box]]` argument: the owning
# element slot is applied to the ranking's winner only, so the declared
# `Iterable[T]` still ranks and `map` finds no overload
# (BUGS.md#map-owned-iterable-own-callback-no-overload).
from typing import Iterable
from tpy import int32, Own


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def take(b: Own[Box]) -> int32:
    return b.v


def run(it: Iterable[Own[Box]]) -> None:
    print(list(map(take, it)))  # tpyc: error(/No matching overload for 'map'/)


run([Box(1)])
