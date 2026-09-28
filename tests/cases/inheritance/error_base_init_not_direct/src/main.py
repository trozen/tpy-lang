# A `G.__init__(self)` naming a grandparent is rejected: CPython runs G's
# initializer and skips M's, while C++ can only construct a direct base
# (LANGUAGE_FEATURES.md "Single class inheritance").
from tpy import int32


class G:
    g: int32

    def __init__(self) -> None:
        print("G init")
        self.g = 1


class M(G):
    m: int32

    def __init__(self) -> None:
        super().__init__()
        print("M init")
        self.m = 2


class C(M):
    def __init__(self) -> None:
        G.__init__(self)  # tpyc: error(/'G' is not a direct base of 'C'; call 'super\(\)\.__init__\(\.\.\.\)' or 'M\.__init__\(self, \.\.\.\)'/)
        print("C init")


def main() -> None:
    c = C()
    print(c.g)


main()
