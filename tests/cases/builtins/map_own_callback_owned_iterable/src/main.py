# An `Own[Box]` callback over an `Iterable[Own[Box]]` argument: the
# callback's declared parameter binds `map`'s `T = Own[Box]`, so its
# iterable slot hands each element over to the callback.
from typing import Iterable
from tpy import int32, Own


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def take(b: Own[Box]) -> int32:
    return b.v


def run(it: Iterable[Own[Box]]) -> None:
    print(list(map(take, it)))  # tpyc: ok


run([Box(1)])
