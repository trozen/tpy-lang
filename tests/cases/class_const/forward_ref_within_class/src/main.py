# Forward references between class constants in the same body work:
# C++ static constexpr members declared earlier in a class scope are visible
# to later declarations in the same class.
from typing import Final
from tpy import int32


class Limits:
    BASE: Final[int32] = 10
    DOUBLE: Final[int32] = BASE * 2
    TRIPLE: Final[int32] = BASE + DOUBLE


def main() -> None:
    print(Limits.BASE)
    print(Limits.DOUBLE)
    print(Limits.TRIPLE)


main()
