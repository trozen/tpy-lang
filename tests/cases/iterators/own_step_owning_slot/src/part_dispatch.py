# `@dispatch` and argument-taking `__next__` / `__iter__`: an owning
# generator resolves to the owning variant of a pair; a `__next__(index)`
# or `__iter__(mode)` is no step, nor is an `__iter__(*, mode)`; a
# defaulted keyword-only parameter keeps the member a step.
from typing import Iterable, Iterator, Protocol, Self
from tpy import Own, dispatch, int32
from fixtures import Cell, Tok, make, take_tok


ARG_CELL = Cell(42)
ARG_TOK = Tok(42)


class ArgTokBag:
    @dispatch
    def __iter__(self, mode: int32) -> Iterator[Tok]:
        yield ARG_TOK

    @dispatch
    def __iter__(self) -> Iterator[Own[Tok]]:
        yield Tok(8)


class KwTokBag:
    @dispatch
    def __iter__(self, unused: bool) -> Iterator[Own[Tok]]:
        yield Tok(9)

    @dispatch
    def __iter__(self, *, mode: int32) -> Iterator[Tok]:
        yield ARG_TOK

    @dispatch
    def __iter__(self) -> Iterator[Own[Tok]]:
        yield Tok(8)


class KwDefaultTokBag:
    def __iter__(self, *, mode: int32 = 0) -> Iterator[Own[Tok]]:
        yield Tok(7 + mode)


class WithArg(Protocol):
    def __next__(self, index: int32) -> Cell: ...


class ArgCursor:
    def __iter__(self) -> Self:
        return self

    @dispatch
    def __next__(self) -> Own[Cell]:
        if ARG_CELL.v < 0:
            raise StopIteration
        return Cell(1)

    @dispatch
    def __next__(self, index: int32) -> Cell:
        if index < 0:
            raise StopIteration
        return ARG_CELL


def next_at(x: WithArg) -> int32:
    # the conformance at the call is the subject
    return 42


# The owning variant is declared first: the CPython stand-in runs the
# first variant whose parameter types match, without element types.
@dispatch
def pick(xs: Iterable[Own[Cell]]) -> int32:
    n = 0
    for c in xs:
        n += c.v
    return n + 200


@dispatch
def pick(xs: Iterable[Cell]) -> int32:
    return 100


def main() -> None:
    # an owning generator at a `@dispatch` pair resolves to the owning
    # variant: the lending one refuses it
    print("dispatch owning", pick(make(2)))  # tpyc: ok
    # a `__next__` requirement taking an argument is no iteration step, so
    # an argument-less owning `__next__` beside it is not held to it
    print("next with argument", next_at(ArgCursor()))  # tpyc: ok
    # iteration runs the argument-less `__iter__`, which hands its @nocopy
    # steps over; the lending `__iter__(mode)` beside it is no step
    print("iter with argument", take_tok(ArgTokBag()))  # tpyc: ok
    # a required keyword-only `__iter__(*, mode)` is no step either: the
    # argument-less overload hands its @nocopy steps over
    print("iter with keyword argument", take_tok(KwTokBag()))  # tpyc: ok
    # a defaulted keyword-only parameter keeps `__iter__` the step
    print("iter with defaulted keyword", take_tok(KwDefaultTokBag()))  # tpyc: ok
