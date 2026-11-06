# A lambda that builds a new object cannot stand at a Callable whose bare `P`
# return is a borrow; the fix spells the slot Callable[[int32], Own[P]].
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Working bullet "A
# callable's return reads as a def's" ("A callable that builds its result is
# spelled Own[R]").
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    g: Callable[[int32], P] = lambda i: P(i)  # tpyc: error(/this lambda builds a new P, but Callable\[\[int32\], P\] returns a borrow; declare Callable\[\[int32\], Own\[P\]\]/)
    print(g(1).n)


main()
