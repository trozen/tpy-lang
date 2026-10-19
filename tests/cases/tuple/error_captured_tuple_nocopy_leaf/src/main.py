# A closure returning a captured tuple whose leaf is @nocopy has no copy to
# make: the closure reads the capture in place, so it cannot move it out
# (docs/LANGUAGE_FEATURES.md, "Borrow Form vs Storage Form": a captured tuple
# lends every reference leaf).
from tpy import int32, Own, nocopy


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


def main() -> None:
    t = (N(1), 3)

    def inner() -> Own[tuple[N, int32]]:
        return t  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage \(tuple element 0\) \(a closure reads its captured value in place/)

    print(inner()[1])


main()
