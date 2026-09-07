# A user-record `__setitem__` whose KEY is a composite over a local that widens
# later in the body: the key slot runs the same body-wide disposition.
from tpy import Int32


def gi() -> int:
    return 1


class Bag:
    xs: list[Int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3, 4]

    def __getitem__(self, i: Int32) -> Int32:
        return self.xs[i]

    def __setitem__(self, i: Int32, v: Int32) -> None:
        self.xs[i] = v


def probe(b: Bag) -> None:
    p = 0
    b[p + 1] = 5  # tpyc: error(/setitem\.family/)
    p = gi()
    print(p)


def main() -> None:
    probe(Bag())


main()
