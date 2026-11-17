# A tuple element returned by reference from an `Own` parameter points into
# the function's own copy (docs/LANGUAGE_FEATURES.md: a parameter the
# function holds its own copy of).
from tpy import Own, int32


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


def pair(x: Own[R]) -> tuple[R, int32]:
    return (x, 1)  # tpyc: error(/Cannot return parameter 'x' \(the function's own copy\) as tuple element 0/)


def main() -> None:
    print(pair(R(1))[1])


main()
