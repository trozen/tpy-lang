# A `super().__init__(name)` arg binds a `const T&` base slot and renders the
# bare name, and the families that bind that way are the reference axis -- so a
# bytearray arg passes like any other member of it.
from tpy import Int32


class Base:
    buf: bytearray
    xs: list[Int32]

    def __init__(self, b: bytearray, xs: list[Int32]) -> None:
        self.buf = b  # tpyc: warning(/copies bytearray into field/)
        self.xs = xs  # tpyc: warning(/copies list\[Int32\] into field/)


class Child(Base):
    n: Int32

    def __init__(self, b: bytearray, xs: list[Int32]) -> None:
        super().__init__(b, xs)  # tpyc: ok
        self.n = 1


def main() -> None:
    # The field write itself is a warned copy (both legs), so the case pins
    # the warning rather than aliasing: what flipped is the base-init ARG.
    c = Child(bytearray(b"xy"), [1, 2, 3])
    print(len(c.buf), len(c.xs), c.n)


main()
