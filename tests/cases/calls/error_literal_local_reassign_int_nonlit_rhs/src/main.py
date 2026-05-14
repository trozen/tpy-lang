# Reassigning a `Literal[int]`-annotated local from a non-Literal-typed source
# is rejected for the same reason as the init case.
from typing import Literal
from tpy import Int32


def get_n() -> Int32:
    return Int32(5)


def main() -> None:
    n: Literal[1, 2] = 1
    n = get_n()  # tpyc: error(/Literal\[1, 2\].*Int32/)
    print(n)


main()
