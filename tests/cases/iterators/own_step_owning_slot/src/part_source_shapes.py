# Further owning source shapes: an `__iter__` overload set, a whole
# `Own[tuple[...]]` step, a class naming `Iterable[Own[Cell]]` as base, and
# a returned generic iterator, whose own declaration alone hands over.
from typing import Iterable, Self
from tpy import Own, dispatch, int32, readonly
from fixtures import Cell, Tok, TokCursor, take, take_tok


class TokSource:
    count: int32

    def __init__(self) -> None:
        self.count = 0

    @dispatch
    def __iter__(self) -> Own[TokCursor]:
        # writes the receiver, so the pair is a mutable / readonly twin
        # (BUGS.md#readonly-twin-mutable-inferred-const)
        self.count += 1
        return TokCursor()

    @dispatch
    @readonly
    def __iter__(self) -> Own[TokCursor]:
        return TokCursor()


class Whole:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[tuple[Cell, int32]]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return (Cell(self.n), self.n)


def take_whole(xs: Iterable[Own[tuple[Cell, int32]]]) -> int32:
    s = 0
    for t in xs:
        t[0].v += 1
        s += t[0].v + t[1]
    return s


class OwnCounter(Iterable[Own[Cell]]):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[Cell]:
        if self.n >= 2:
            raise StopIteration
        self.n += 1
        return Cell(self.n + 20)


class Popper[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> Own[T]:
        if len(self.items) == 0:
            raise StopIteration
        return self.items.pop()


class PopBag:
    def __iter__(self) -> Own[Popper[Tok]]:
        toks: list[Tok] = []
        toks.append(Tok(5))
        return Popper(toks)


class Holder[T]:
    item: T
    done: bool

    def __init__(self, item: Own[T]) -> None:
        self.item = item
        self.done = False

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> T:
        if self.done:
            raise StopIteration
        self.done = True
        return self.item


class HolderBag:
    def __iter__(self) -> Own[Holder[Own[Cell]]]:
        return Holder[Own[Cell]](Cell(3))


def first_lent(xs: Iterable[Cell]) -> int32:
    for x in xs:
        return x.v
    return 0


def main() -> None:
    # an `__iter__` overload set whose every overload hands its steps over
    ts = TokSource()
    print("overload set", take_tok(ts), take_tok(ts))  # tpyc: ok
    # a whole `Own[tuple[...]]` step hands every leaf over
    print("whole tuple", take_whole(Whole()))  # tpyc: ok
    # a class naming `Iterable[Own[Cell]]` as its base, handing over
    print("explicit owning base", take(OwnCounter()))  # tpyc: ok
    # a returned generic iterator whose own `__next__ -> Own[T]` hands
    # its @nocopy steps over
    print("returned generic owning", take_tok(PopBag()))  # tpyc: ok
    # an `Own` substituted into a returned iterator's `__next__ -> T` hands
    # nothing over, so the source fits a lending slot
    print("returned generic substituted Own", first_lent(HolderBag()))  # tpyc: ok
    # at an owning slot the substituted `Own` still lends, so each step is
    # copied
    print("returned generic substituted Own at owning slot", take(HolderBag()))  # tpyc: warning(/copies Cell elements/)
