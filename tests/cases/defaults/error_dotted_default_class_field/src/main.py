# A dotted method default whose head the class body binds to a field
# before the def reads that field (`R.real` is the int 11's, CPython prints
# 119 110), never the module's class `R`; it names no member a default may,
# so it is rejected.
from typing import Final
from tpy import int32

K: Final[int32] = 7


class R:
    real: Final[int32] = 7

    def __init__(self) -> None:
        pass


class C:
    R: int32 = 11

    def __init__(self) -> None:
        pass

    # The subject: `R` is the field above, not the module's class.
    def m(self, x: int32 = R.real, y: int32 = K) -> int32:  # tpyc: error(/Default parameter value 'R.real' must name an enum member, a class constant or a module-level Final\[T\] constant; the class body binds 'R' to a field/)
        return x * 10 + y


def main() -> None:
    c = C()
    print(c.m(y=9), c.m())


main()
