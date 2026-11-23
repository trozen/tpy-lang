# A position spelled at a pending construction is matched against, never
# merged into: an owning callable against the spelled `A = P` is refused.
from __future__ import annotations
from typing import Callable
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> Own[P]:
    return P(1)


class Duo[A, B]:
    f: Callable[[], A] | None
    b: list[B]

    def __init__(self) -> None:
        self.f = None
        self.b = []

    def set_f(self, f: Callable[[], A]) -> None:
        self.f = f

    def add_b(self, y: B) -> None:
        self.b.append(y)


def main() -> None:
    own_mk: Callable[[], Own[P]] = mk
    d = Duo[P, _]()
    d.set_f(own_mk)  # tpyc: error(/Cannot infer 'Own' for type argument 'A' of 'Duo' from a later call on 'd'; spell the class type arguments: Duo\[Own\[P\], B\]\(\.\.\.\)/)
    d.add_b(7)
    print(len(d.b))


main()
