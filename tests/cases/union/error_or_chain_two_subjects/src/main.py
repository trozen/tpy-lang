# `isinstance(v, Alpha) or isinstance(w, Beta)` -- an or-chain over two
# DIFFERENT union subjects: not lowered yet, so the case pins the reject.
from tpy import Int32


class Alpha:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Beta:
    y: Int32

    def __init__(self, y: Int32) -> None:
        self.y = y


def probe(v: Alpha | Beta, w: Alpha | Beta) -> Int32:
    # Two DIFFERENT subjects across the or-chain leaves: there is no
    # single-subject chain to narrow on, so the condition falls out of the
    # narrowing lane and its leaves reject as plain boolean operands.
    if isinstance(v, Alpha) or isinstance(w, Beta):  # tpyc: error(/if.cond_binop/)
        return 1
    return 0


def main() -> None:
    print(probe(Alpha(1), Beta(2)))


main()
