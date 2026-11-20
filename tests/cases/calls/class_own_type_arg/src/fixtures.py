# A reference class and a generic class written in TPy, shared by the parts.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class C[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)

    def fill(self, f: Callable[[int32], T], k: int32) -> None:
        self.items.append(f(k))

    def get(self) -> T:
        return self.items[0]

    @classmethod
    def build(cls, f: Callable[[int32], T], k: int32) -> Own[C[T]]:
        c = C[T]()
        c.items.append(f(k))
        return c


def mk(k: int32) -> Own[P]:
    return P(k)
