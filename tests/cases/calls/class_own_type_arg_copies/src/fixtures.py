# Classes shared by the parts: a reference class and generic classes written
# in TPy whose members copy their `T` where it is `Own[P]`.
from __future__ import annotations
from typing import Iterator
from tpy import int32


class P:
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


class C[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def get(self) -> T:
        return self.items[0]
