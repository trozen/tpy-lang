# Shared by the copy_iter_signature parts: a reference element and the
# owning / lending slots a copy_iter call meets.
from typing import Iterable
from tpy import int32, Own


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


def take(xs: Iterable[Own[Cell]]) -> Own[list[Cell]]:
    out: list[Cell] = []
    for c in xs:
        out.append(c)
    return out


def bump_all(xs: Iterable[Own[Cell]]) -> int32:
    n = 0
    for c in xs:
        c.v += 100
        n += c.v
    return n
