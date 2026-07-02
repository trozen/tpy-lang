# Regression: an aliased-imported enum and its LOCAL same-named twin stay
# distinct under for-each iteration and inside list[...] annotations.
from enum import IntEnum
from colors import Color as ForeignColor


class Color(IntEnum):
    BLUE = 1
    CYAN = 2
    MAGENTA = 3


def sum_foreign(cs: list[ForeignColor]) -> int:
    total = 0
    for c in cs:
        total += c.value
    return total


def sum_local(cs: list[Color]) -> int:
    total = 0
    for c in cs:
        total += c.value
    return total


def main() -> None:
    fcount = 0
    for c in ForeignColor:
        fcount += 1
    lcount = 0
    for c in Color:
        lcount += 1
    print("foreign count", fcount)
    print("local count", lcount)
    print("foreign sum", sum_foreign([ForeignColor.RED, ForeignColor.GREEN]))
    print("local sum", sum_local([Color.BLUE, Color.CYAN, Color.MAGENTA]))


main()
