# A pointer-Optional walrus over a ternary needs both arms to be live lvalues:
# a fresh ctor arm would have the target hold the address of a temporary that
# dies at the end of the full expression, so the shape must keep rejecting.
from tpy import int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(c: bool) -> int32:
    if (t := (Rec(1) if c else None)) is not None:  # tpyc: error(/expr\.walrus/)
        return t.n
    return -1


def main() -> None:
    print(f(True))


main()
