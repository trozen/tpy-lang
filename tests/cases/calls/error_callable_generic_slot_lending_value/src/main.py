# A lending Callable value at a Callable parameter whose declared return is a
# type parameter is refused: the generic slot keeps what each call hands
# back, so the borrow would be copied.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the `Callable` -> `Fn`
# fit ("A slot whose declared return is an open type parameter keeps
# its earlier fit").
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ident(p: P) -> P:
    return p


def pick[T, U](f: Callable[[T], U], x: T) -> Callable[[T], U]:
    return f


def main() -> None:
    g: Callable[[P], P] = ident
    p = P(1)
    # The subject: g lends at a slot whose return is U.
    h = pick(g, p)  # tpyc: error(/it returns a borrow of P where the Callable keeps its own P/)
    h(p).n += 7
    print(p.n)


main()
