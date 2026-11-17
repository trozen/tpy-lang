# An iterator object at an owning sub-protocol slot with a member beyond
# iteration (`Counted[Own[R]]` adds `__len__`) does not conform: an iteration
# adapter conforms only to a protocol whose whole contract it meets
# (docs/LANGUAGE_FEATURES.md "Copy Warnings for Inline Storage").
from typing import Iterable, Protocol
from tpy import Own, int32


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


class Counted[T](Iterable[T], Protocol):
    def __len__(self) -> int32: ...


def mk() -> Own[list[R]]:
    return [R(1), R(2)]


def take_counted(xs: Counted[Own[R]]) -> int32:
    return len(xs)


def main() -> None:
    print(take_counted(iter(mk())))  # tpyc: error(/does not conform to protocol Counted/)


main()
