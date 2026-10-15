from __future__ import annotations

from typing import Iterator
from tpy import int32


class Stock:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [3, 4]

    # A plain class's `-> Iterator[...]` body building a later class; it is
    # long enough that other members of this size go to the .cpp, yet another
    # module's loop deduces its return, so it is defined in the header.
    def __iter__(self) -> Iterator[int32]:
        a = Unit(1).k  # tpyc: ok
        b = Unit(2).k
        c = a + b
        d = c * 2
        e = d - a
        f = e + b
        g = f - c
        self.items.append(g)
        return iter(self.items)


class Unit:
    def __init__(self, k: int32) -> None:
        self.k = k
