# A consuming method's field read outside a `return` is a borrow
# (docs/LANGUAGE_FEATURES.md, "Consuming Methods"), so a @nocopy field at an
# Own tuple member cannot be copied into it and errors. CPython runs this (it
# hands the same object over); a @nocopy value can only be moved, and the
# field is read after the call -- a documented divergence.
from typing import Self
from tpy import int32, Own, nocopy


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def take(t: Own[tuple[Tok, Tok]]) -> int32:
    a, b = t
    return a.n + b.n


class H:
    a: Tok
    b: Tok

    def __init__(self) -> None:
        self.a = Tok(1)
        self.b = Tok(2)

    def use(self: Own[Self]) -> int32:
        r = take((self.a, self.b))  # tpyc: error(/@nocopy type 'Tok' cannot be passed as argument 't' tuple element 0/)
        return r + self.a.n


def main() -> None:
    print(H().use())


main()
