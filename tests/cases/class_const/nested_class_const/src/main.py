# Class constants on a nested class. The owner name carries a dotted form
# (`Outer.Inner`) and codegen must translate it to `Outer::Inner::LIMIT`,
# not the invalid `Outer.Inner::LIMIT`.
from typing import Final
from tpy import Int32


class Outer:
    class Inner:
        LIMIT: Final[Int32] = 42
        TAG: Final[str] = "inner"


def main() -> None:
    print(Outer.Inner.LIMIT)
    print(Outer.Inner.TAG)


main()
