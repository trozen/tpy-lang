# `Char` record fields: reads, writes and a comparison against a one-character
# string literal all render like any other scalar field.
from tpy import Char


class P:
    c: Char

    def __init__(self, c: Char) -> None:
        self.c = c

    def get(self) -> Char:
        return self.c

    def put(self, c: Char) -> None:
        self.c = c


def swap(p: P, z: Char) -> Char:
    x = p.c
    # The field write aliases the caller's record.
    p.c = z
    return x


def show(p: P) -> None:
    # A Char field compared against a string literal.
    if p.c == "x":
        print(p.c)


def main() -> None:
    z: Char = "z"
    x: Char = "x"
    p = P(x)
    show(p)
    print(swap(p, z))
    print(p.get())
    p.put(x)
    show(p)


main()
