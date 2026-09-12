# `Literal[int]`-annotated local rejects a non-literal-source RHS for the same
# reason as the str variant.
from typing import Literal
from tpy import int32


def get_int() -> int32:
    return int32(5)


def main() -> None:
    n: Literal[1, 2] = get_int()  # tpyc: error(/Literal\[1, 2\].*int32/)
    print(n)


main()
