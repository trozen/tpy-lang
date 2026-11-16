# An `__iter__` overload set (lending first) where one overload C++ may select
# returns an iterator handing over its steps cannot fill a lending
# `Iterable[Cell]` parameter.
# Rule: LANGUAGE_FEATURES "An owning iterator fits only an owning slot".
from typing import Iterable, Self
from tpy import Own, dispatch, int32, readonly


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


FALLBACK = Cell(99)


class LendCursor:
    done: bool

    def __init__(self) -> None:
        self.done = False

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Cell:
        if self.done:
            raise StopIteration
        self.done = True
        return FALLBACK


class OwnCursor:
    done: bool

    def __init__(self) -> None:
        self.done = False

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[Cell]:
        if self.done:
            raise StopIteration
        self.done = True
        return Cell(1)


class DualBag:
    calls: int32

    def __init__(self) -> None:
        self.calls = 0

    @dispatch
    @readonly
    def __iter__(self) -> Own[LendCursor]:
        return LendCursor()

    @dispatch
    def __iter__(self) -> Own[OwnCursor]:
        # writes the receiver, so the pair is a mutable / readonly twin
        # (a non-writing one fails the C++ build:
        # BUGS.md#readonly-twin-mutable-inferred-const)
        self.calls += 1
        return OwnCursor()


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def main() -> None:
    print(first(DualBag()).v)  # tpyc: error(/Type DualBag does not conform to protocol Iterable\[Cell\].*hands over its steps/)


main()
