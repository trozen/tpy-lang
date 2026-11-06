# A class used as a callable builds a new object, so it cannot stand at a
# Callable whose bare `Widget` return is a borrow; the fix spells Own[Widget].
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Working bullet "A
# callable's return reads as a def's" ("A callable that builds its result is
# spelled Own[R]").
from typing import Callable
from tpy import int32


class Widget:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    fac: Callable[[int32], Widget] = Widget  # tpyc: error(/'Widget' builds a new Widget, but Callable\[\[int32\], Widget\] returns a borrow; declare Callable\[\[int32\], Own\[Widget\]\]/)
    print(fac(1).n)


main()
