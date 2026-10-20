# A repeated @nocopy tuple name inside a returned literal has no copy to make: a
# located error (docs/LANGUAGE_FEATURES.md, BUGS.md#tuple-literal-name-member-silent-copy).
from tpy import Own, int32, nocopy


@nocopy
class N:
    def __init__(self, x: int32) -> None:
        self.x = x


def pair() -> tuple[tuple[Own[N], int32], tuple[Own[N], int32]]:
    t = (N(1), 2)
    return (t, t)  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage \(tuple element 0\.0\)/)


def main() -> None:
    r = pair()
    print(r[0][1])


main()
