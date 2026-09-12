# Reassigning a `Literal[int]`-annotated local from a non-Literal-typed source
# is rejected for the same reason as the init case.
from typing import Literal
from tpy import int32


def get_n() -> int32:
    return int32(5)


def main() -> None:
    n: Literal[1, 2] = 1
    n = get_n()  # tpyc: error(/Literal\[1, 2\].*int32/)
    print(n)


main()
