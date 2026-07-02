# An aliased-imported enum must not be confused with an unrelated LOCAL
# enum of the same declared name, as params/returns/var-decls.
from enum import IntEnum
from colors import Color as ForeignColor


class Color(IntEnum):
    BLUE = 42


def local_roundtrip(c: Color) -> Color:
    return c


def foreign_roundtrip(c: ForeignColor) -> ForeignColor:
    return c


def main() -> None:
    local: Color = Color.BLUE
    foreign: ForeignColor = ForeignColor.RED
    print(local.name, local.value)
    print(foreign.name, foreign.value)
    print(local_roundtrip(Color.BLUE).name, local_roundtrip(Color.BLUE).value)
    print(foreign_roundtrip(ForeignColor.GREEN).name, foreign_roundtrip(ForeignColor.GREEN).value)


main()
