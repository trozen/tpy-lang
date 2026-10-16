# An int Literal value whose set does not fit a narrower fixed-width
# parameter is refused, as its storage type is, at every spelling.
from typing import Literal
from tpy import int8, int32


class S:
    def __init__(self) -> None:
        pass

    def __mul__(self, x: int8) -> int32:
        return int32(x)


def pick(f: bool) -> Literal[300, 5]:
    if f:
        return 300
    return 5


def main() -> None:
    s = S()
    k = pick(True)
    print(s * k)  # tpyc: error(/expected int8, got int32/)


main()
