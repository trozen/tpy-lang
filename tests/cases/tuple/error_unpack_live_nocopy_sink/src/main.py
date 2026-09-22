# A @nocopy element unpacked off a LIVE source is a borrow, so feeding it to
# an Own sink asks for a copy that a @nocopy type refuses -- the scalar rule.
from tpy import int32, Own, nocopy


@nocopy
class NBox:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def nmk() -> tuple[Own[NBox], int32]:
    return (NBox(1), 1)


def nsink(x: Own[NBox]) -> int32:
    return x.n


def main() -> None:
    t = nmk()
    x, k = t
    print(nsink(x))  # tpyc: error(/cannot be copied/)
    print(t[0].n, k)


main()
