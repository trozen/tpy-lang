# `char` record fields: reads, writes and a comparison against a one-character
# string literal all render like any other scalar field.
from tpy import char


class P:
    c: char

    def __init__(self, c: char) -> None:
        self.c = c

    def get(self) -> char:
        return self.c

    def put(self, c: char) -> None:
        self.c = c


def swap(p: P, z: char) -> char:
    x = p.c
    # The field write aliases the caller's record.
    p.c = z
    return x


def show(p: P) -> None:
    # A char field compared against a string literal.
    if p.c == "x":
        print(p.c)


def main() -> None:
    z: char = "z"
    x: char = "x"
    p = P(x)
    show(p)
    print(swap(p, z))
    print(p.get())
    p.put(x)
    show(p)


main()
