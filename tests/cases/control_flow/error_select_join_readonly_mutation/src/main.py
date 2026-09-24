# A select with a readonly operand is readonly itself, so mutating through
# the joined result rejects even when the other operand is mutable.
from tpy import int32, readonly


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick(c: bool, ro: readonly[C], plain: C) -> None:
    r = ro if c else plain
    # `r` may alias the readonly `ro`.
    r.n += 1  # tpyc: error(/readonly/)
    print(r.n)


def main() -> None:
    pick(True, C(1), C(2))


main()
