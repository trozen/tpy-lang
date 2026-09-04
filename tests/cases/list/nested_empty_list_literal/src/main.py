# An EMPTY list literal nested inside another list literal: the outer brace
# supplies the element type, so the inner one needs no spelling of its own.
from tpy import Int32


def rows() -> Int32:
    xs: list[list[Int32]] = [[], [1]]   # tpyc: ok -- an empty nested element
    xs[0].append(9)                     # the empty row is a real, mutable list
    return len(xs) * 10 + len(xs[0])


def main() -> None:
    print(rows())


main()
