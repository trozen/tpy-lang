# A slice ASSIGN whose bounds are runtime BigInt values: the Slice / BasicSlice
# members are fixed-int, so each bound narrows exactly as the slice READ does.
from tpy import Int32, Own


class Holder:
    xs: list[Int32]

    def __init__(self) -> None:
        self.xs = list(range(0, 10))

    def blank(self, lo: int, step: int) -> None:
        self.xs[lo::step] = [0, 0]  # tpyc: ok


def stepped(lo: int, step: int) -> Own[list[Int32]]:
    xs = list(range(0, 12))
    xs[lo::step] = [90, 91, 92]  # tpyc: ok
    return xs


def basic(lo: int, hi: int) -> Own[list[Int32]]:
    xs = list(range(0, 12))
    # A non-stepped bound pair narrows the same way, and step 1 may resize.
    xs[lo:hi] = [70, 71, 72, 73]  # tpyc: ok
    return xs


def upper_only(hi: int) -> Own[list[Int32]]:
    xs = list(range(0, 6))
    xs[:hi] = [50]  # tpyc: ok
    return xs


def main() -> None:
    print(stepped(2, 4))
    print(basic(3, 6))
    print(upper_only(4))
    h = Holder()
    h.blank(1, 5)
    # The field mutation is observed through the receiver, not a copy.
    print(h.xs)


main()
