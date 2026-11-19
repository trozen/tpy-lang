# Iteration: a generator over a callable's result lends at U = P and hands
# over at U = Own[P]; builtin map forwards the callable's form.
from __future__ import annotations
from typing import Iterable, Iterator, Self
from tpy import Fn, Own, int32
from fixtures import P, identity, mk


def my_map[T, U](fn: Fn[[T], U], xs: Iterable[T]) -> Iterator[U]:
    for x in xs:
        yield fn(x)  # tpyc: ok


def my_map_local[T, U](fn: Fn[[T], U], xs: Iterable[T]) -> Iterator[U]:
    for x in xs:
        y = fn(x)
        yield y  # tpyc: ok


def main() -> None:
    # generator: each step lends the source's element
    ps = [P(1), P(2)]
    for q in my_map(identity, ps):
        q.n += 10
    print("gen-lend", ps[0].n, ps[1].n)
    # generator frame local holding the callable's result lends too
    for q in my_map_local(identity, ps):
        q.n += 1
    print("gen-local", ps[0].n, ps[1].n)
    # generator: each owned step moves into the list
    keep: list[P] = []
    for q in my_map(mk, ps):
        keep.append(q)  # tpyc: ok
    keep[0].n += 100
    print("gen-own", ps[0].n, keep[0].n)
    # builtin map: a lending callable lends the elements
    for q in map(identity, ps):
        q.n += 50
    print("map-lend", ps[0].n)
    only_main()


class Only:
    items: list[P]

    def __init__(self) -> None:
        self.items = [P(5), P(6)]

    def __iter__(self: Own[Self]) -> Iterator[Own[P]]:
        while self.items:
            yield self.items.pop(0)


def take(p: Own[P]) -> int32:
    return p.n


def only_main() -> None:
    # an owning callback binds map's T = Own[P]: a consuming-only iterable fits
    print("map-only", list(map(take, Only())))  # tpyc: ok
