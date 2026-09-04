# A generator expression inside a constructor member-init list: the genexpr's
# IIFE captures the ctor PARAM it reads, at a plain tuple field and at a nested
# tuple field.
from tpy import Int32


class H:
    t: tuple[Int32, Int32]

    def __init__(self, d: dict[Int32, Int32]) -> None:
        # The genexpr reads `d`, so its lambda must capture it.
        self.t = (sum(k for k in d), 1)


class N:
    t: tuple[Int32, tuple[Int32, str]]

    def __init__(self, d: dict[Int32, Int32]) -> None:
        # ... the same capture one tuple level deeper.
        self.t = (1, (sum(k for k in d), "a"))

    def inner(self) -> Int32:
        a, b = self.t
        c, s = b
        return c


def main() -> None:
    h = H({1: 2, 5: 6})
    a, b = h.t
    print(a, b)
    n = N({3: 4, 7: 8})
    print(n.inner())


main()
