# `copy_iter` hands its steps over, so a LENDING `Iterable[Cell]` parameter
# refuses it: the callee may keep a step past it, which would dangle
# (docs/LANGUAGE_FEATURES.md "Copy Warnings for Inline Storage").
from typing import Iterable
from tpy import int32, copy_iter


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


def first(xs: Iterable[Cell]) -> Cell:
    for c in xs:
        return c
    raise ValueError("empty")


def main() -> None:
    cells = [Cell(1), Cell(2)]
    # the refused call, with the remedy: an owning parameter, or collect first
    print(first(copy_iter(cells)).v)  # tpyc: error(/hands over its steps; declare the parameter 'Iterable\[Own\[Cell\]\]' to take them, or collect them first/)


main()
