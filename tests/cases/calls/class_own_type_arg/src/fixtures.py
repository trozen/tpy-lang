# Classes shared by the parts: generic classes written in TPy whose `T` the
# sections instantiate at `Own[P]` (each member's copy reported there).
from __future__ import annotations
from typing import Callable, Iterator
from tpy import Own, int32, nocopy


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Box[T]:
    v: T
    items: list[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []

    def get(self) -> T:
        # at `Own[P]` an owned copy of the stored value
        return self.v

    def push(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)

    def push_v(self) -> None:
        # the class's `T` handed on to its own `T` parameter
        self.push(self.v)

    def __iter__(self) -> Iterator[T]:
        for it in self.items:
            # at `Own[P]` each step is an owned copy
            yield it


# The class without a getter: at `Own[N]` nothing copies.
class Bag[T]:
    v: T
    items: list[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []

    def push(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)


class Holder[T]:
    f: Callable[[], T]

    def __init__(self, f: Callable[[], T]) -> None:
        self.f = f

    def get(self) -> T:
        return self.f()


def mk() -> Own[P]:
    return P(2)
