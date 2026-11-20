# Positions found in review round 5: a static or class method whose call
# infers the class's `T = P` through an `Own[T]` parameter, a class used at
# `Own[X]` above its own declaration and inside its own methods, a generic
# alias whose parameter reaches the class (`Tree[int32, Own[P]]` keeps the
# list's `B`), and a non-generic alias over the class. Each lends as `C[P]`.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32
from fixtures import C, P, mk


class User:
    # a field spelled before its class is declared
    held: Later[Own[P]]  # tpyc: warning(/`Own\[P\]` in a type argument of class 'Later' has no effect yet; write `Later\[P\]`/)

    def __init__(self, held: Later[P]) -> None:
        self.held = held  # tpyc: warning(/copies Later\[P\] into field/)


class Later[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def get(self) -> T:
        return self.items[0]

    def peek_own(self, other: Later[Own[P]]) -> int32:  # tpyc: warning(/has no effect yet/)
        # the class at `Own[P]` in its own method
        h = other.get()
        h.x = 9
        return other.items[0].x

    @staticmethod
    def pick(f: Callable[[int32], Own[T]], xs: list[T]) -> T:
        return xs[0]


type Pair[A, B] = C[A] | list[B] | list[Pair[A, B]]

type CP = C[Own[P]]  # tpyc: warning(/has no effect yet/)


def mixed(t: Pair[int32, Own[P]]) -> int32:  # tpyc: ok
    # `A` reaches the class, `B` only a list: `B` keeps its `Own`, nothing
    # is dropped, nothing warned
    return 1


# the parser expands the alias at each use, which warns again
# (BUGS.md#class-own-type-arg-warning-count)
def by_cp(c: CP) -> int32:  # tpyc: warning(/has no effect yet/)
    h = c.get()
    h.x = 9
    return c.items[0].x


def static_call(f: Callable[[int32], Own[P]]) -> None:
    # `f`'s `Own[P]` result binds the class's `T` to `P` through the
    # parameter's `Own[T]`: the result lends
    xs = [P(1)]
    h = Later.pick(f, xs)  # tpyc: ok
    h.x = 9
    print("static", xs[0].x)


def main() -> None:
    static_call(mk)
    later = Later[P]()
    later.items.append(P(1))
    print("own-method", later.peek_own(later))
    u = User(later)
    print("field-before", len(u.held.items))
    c = C[P]()
    c.items.append(P(1))
    print("alias-cp", by_cp(c))
