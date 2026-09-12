# A generator expression inside a constructor member-init list: the genexpr's
# IIFE captures the ctor PARAM it reads, at a plain tuple field and at a nested
# tuple field.
from tpy import int32


class H:
    t: tuple[int32, int32]

    def __init__(self, d: dict[int32, int32]) -> None:
        # The genexpr reads `d`, so its lambda must capture it.
        self.t = (sum(k for k in d), 1)


class N:
    t: tuple[int32, tuple[int32, str]]

    def __init__(self, d: dict[int32, int32]) -> None:
        # ... the same capture one tuple level deeper.
        self.t = (1, (sum(k for k in d), "a"))

    def inner(self) -> int32:
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
