# Protocol values forwarded to an owning slot: the forward passes the
# argument on, so a live list's element copies are warned at the caller that
# passes it, not at the forward.
from typing import Iterable, Iterator, Protocol
from tpy import Own, int32
from fixtures import Bag, Cell, Counter, take


class OwnCells(Iterable[Own[Cell]], Protocol):
    pass


def take_sub(xs: OwnCells) -> int32:
    # the sub-protocol is an `Iterable[Own[Cell]]` at its declared args; the
    # forward passes the argument on, so any copy is the caller's (warned there)
    return take(xs)  # tpyc: ok


def sum_own(xs: Iterable[Own[Cell]]) -> int32:
    s = 0
    for c in xs:
        s += c.v
    return s


def sum_sub(xs: OwnCells) -> int32:
    return sum_own(xs)  # tpyc: ok


class Cells[T](Iterable[T], Protocol):
    pass


def sum_cells(xs: Cells[Own[Cell]]) -> int32:
    s = 0
    for c in xs:
        s += c.v
    return s


def fwd_cells(xs: Cells[Cell]) -> int32:
    # a generic protocol's lending instance forwarded to its owning one
    # copies the elements (warned)
    return sum_cells(xs)  # tpyc: warning(/copies Cell elements/)


class OwnedCursor(Iterator[Own[Cell]], Protocol):
    def __next__(self) -> Own[Cell]: ...


class OwnedSource(Iterable[Own[Cell]], Protocol):
    def __iter__(self) -> OwnedCursor: ...


class OwnedChild(OwnedSource, Protocol):
    pass


def take_owned(xs: OwnedSource) -> int32:
    s = 0
    for c in xs:
        s += c.v
    return s


def forward_owned(xs: OwnedChild) -> int32:
    # the parent's iterator is an owning `Iterator[Own[Cell]]`: its step
    # hands over, so the child meets its owning parent
    return take_owned(xs)  # tpyc: ok


class Counted(Iterable[int32], Protocol):
    count: int32


class CountedChild(Counted, Protocol):
    pass


class Values:
    count: int32

    def __init__(self) -> None:
        self.count = 7

    def __iter__(self) -> Iterator[int32]:
        yield 1


def count_of(xs: Counted) -> int32:
    s = 0
    for x in xs:
        s += x
    return s


def forward_counted(xs: CountedChild) -> int32:
    return count_of(xs)


def main() -> None:
    # sub-protocol of `Iterable[Own[Cell]]`
    print("sub-protocol", take_sub(Bag()))  # tpyc: ok
    # a dead list through an owning sub-protocol is handed over: no copy
    dead: list[Cell] = [Cell(1), Cell(2)]
    print("dead list sub-protocol", sum_sub(dead))  # tpyc: ok
    dead2: list[Cell] = [Cell(3)]
    print("dead list sub-protocol take", take_sub(dead2))  # tpyc: ok
    # a dead Array (an unannotated literal never grown) likewise
    dead_arr = [Cell(1), Cell(2)]  # tpyc: type(/Array/)
    print("dead array sub-protocol", sum_sub(dead_arr))  # tpyc: ok
    dead_arr2 = [Cell(3)]
    print("dead array sub-protocol take", take_sub(dead_arr2))  # tpyc: ok
    held = [Cell(1), Cell(2)]
    # the live `held` is where the copy is caused, so the warning is here
    print("live list sub-protocol", sum_sub(held))  # tpyc: warning(/copies Cell elements/)
    held[0].v += 1
    print("live list sub-protocol after", held[0].v, len(held))
    # likewise through a generic protocol's lending instance
    print("generic protocol forward", fwd_cells(held))  # tpyc: ok
    held[1].v += 1
    print("generic protocol forward after", held[1].v, len(held))
    # a child protocol forwarded to its owning parent whose iterator
    # redeclares the step `Own`
    print("redeclared next forward", forward_owned(Counter()))  # tpyc: ok
    # a sub-protocol of an iterable protocol with a field forwards to its
    # parent at the same type arguments
    print("field protocol forward", forward_counted(Values()))  # tpyc: ok
