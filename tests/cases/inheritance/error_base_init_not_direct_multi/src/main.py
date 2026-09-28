# A named ancestor past a direct base with its own `__init__` is rejected as
# in error_base_init_not_direct; the hint omits `super().__init__(...)` when
# super() initializes a different direct base (Side here, first in the MRO).
from tpy import int32


class Root:
    r: int32

    def __init__(self, r: int32) -> None:
        self.r = r


class Side:
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s


class Lane(Root):
    def __init__(self) -> None:
        super().__init__(1)


class Both(Side, Lane):
    def __init__(self) -> None:
        super().__init__(2)
        Root.__init__(self, 3)  # tpyc: error(/'Root' is not a direct base of 'Both'; call 'Lane\.__init__\(self, \.\.\.\)'/)


def main() -> None:
    b = Both()
    print(b.r, b.s)


main()
