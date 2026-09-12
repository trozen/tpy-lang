# Writing a scalar field through a record element of a tuple: t[N].field = x and += .
# The borrow tuple aliases the caller's record, so the write is observed on `leaf`
# afterward -- forcing the value-vs-reference distinction (a silent copy into the tuple
# would leave leaf.n at 1 and diverge from CPython on the cpy-parity check).
from tpy import int32


class Leaf:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bump(t: tuple[int32, Leaf]) -> None:
    t[1].n = 5
    t[1].n += 3


def main() -> None:
    leaf = Leaf(1)
    bump((10, leaf))
    print(leaf.n)


main()
