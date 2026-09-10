# A capture re-seated between a PARAM subject and a FUNCTION-LOCAL one has two
# storage roots, and returning it as a borrow-form tuple is safe only if EVERY
# root outlives the call. Resolving the capture to whichever root came first
# would accept `reseated` and hand the caller pointers into the dead local.
# `only_param` is the inverse leg and must keep compiling -- it is checked
# first, so the case reaching its error at all proves the single-root shape
# stays accepted.
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class PBox:
    pair: tuple[Counter, Counter]

    def __init__(self, x: Counter, y: Counter) -> None:
        self.pair = (x, y)


def only_param(pbox: PBox) -> tuple[Counter, Counter]:
    match pbox:
        case PBox(pair=t):
            return t
    return pbox.pair


def reseated(pbox: PBox) -> tuple[Counter, Counter]:
    local = PBox(Counter(8), Counter(9))
    match pbox:
        case PBox(pair=t):
            match local:
                case PBox(pair=t):
                    # `t` reaches pbox.pair on one path and local.pair on the
                    # other; the second dies with the call
                    return t  # tpyc: error(/storage owned by the function/)
    return pbox.pair


def main() -> None:
    p = PBox(Counter(1), Counter(2))
    a, b = only_param(p)
    print(a.n, b.n)


main()
