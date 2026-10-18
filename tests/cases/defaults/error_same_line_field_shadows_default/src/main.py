# A dataclass field default whose head an earlier statement on the same
# line binds to a field reads that field (`R.real` is the int 11's, CPython
# prints 11 11 22 11), never the module's class `R`; it names no member a
# default may, so it is rejected.
from dataclasses import dataclass
from typing import Final
from tpy import int32


class R:
    real: Final[int32] = 7

    def __init__(self) -> None:
        pass


@dataclass
class C:
    # The subject: `R` is the field before the semicolon, not the class.
    R: int32 = 11; x: int32 = R.real  # tpyc: error(/Default field value 'R.real' must name an enum member, a class constant or a module-level Final\[T\] constant; the class body binds 'R' to a field/)


def main() -> None:
    c = C()
    d = C(R=22)
    print(c.R, c.x, d.R, d.x)


main()
