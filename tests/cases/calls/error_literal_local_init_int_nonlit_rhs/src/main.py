# `Literal[int]`-annotated local rejects a non-literal-source RHS for the same
# reason as the str variant.
from typing import Literal
from tpy import Int32


def get_int() -> Int32:
    return Int32(5)


def main() -> None:
    n: Literal[1, 2] = get_int()  # tpyc: error(/Literal\[1, 2\].*Int32/)
    print(n)


main()
