# A union tuple leaf copies the alternative it holds, so a @nocopy alternative
# leaves an owning slot no copy to make (docs/LANGUAGE_FEATURES.md, "Borrow
# Form vs Storage Form": the per-element rule covers a union leaf).
from tpy import int32, Own, nocopy


class B:
    def __init__(self, w: int32) -> None:
        self.w = w


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


def pick(x: tuple[N | B, int32], y: tuple[N | B, int32],
         c: bool) -> Own[tuple[N | B, int32]]:
    # The select lends element 0 of either parameter into the owned result.
    return x if c else y  # tpyc: error(/cannot copy non-copyable type '(N \| B|B \| N)' into owned storage \(tuple element 0\)/)


def main() -> None:
    r = pick((N(1), 1), (B(2), 2), True)
    print(r[1])


main()
