from __future__ import annotations
from tpy import int32, Ptr

import b

type Tree[T] = int32 | Ptr[T] | list[Tree[T]]


class Bag:
    # The alias stores `b.Item` only behind a Ptr, so the cycle check accepts it.
    value: Tree[b.Item]  # tpyc: ok

    def __init__(self) -> None:
        self.value = 5

    def count(self) -> int32:
        return 5
