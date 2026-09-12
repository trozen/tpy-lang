# Phase 9: T-independent class constants on a generic class. The static
# is emitted on the template (`static constexpr int32_t MAX = 10;` inside
# `template<typename T> struct C { ... }`), and instance-side reads
# render the receiver's parameterized type to land on `C<int32_t>::MAX`.
from typing import Final
from tpy import int32


class C[T]:
    MAX: Final[int32] = 10
    SCALE: Final[float] = 1.5

    def __init__(self) -> None:
        pass


def main() -> None:
    c = C[int32]()
    print(c.MAX)
    print(c.SCALE)
    d = C[float]()
    print(d.MAX)


main()
