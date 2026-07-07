# An enum-member default whose enum differs from the declared parameter type is
# rejected at registration -- mirrors the field-default check, so the two
# default surfaces stay consistent. TPy-only (CPython accepts any object default).
from enum import Enum


class Color(Enum):
    RED = 0
    GREEN = 1


class Mode(Enum):
    A = 0
    B = 1


def paint(c: Color = Mode.A) -> Color:  # tpyc: error(/does not match the declared parameter type/)
    return c


def main() -> None:
    print(int(paint().value))


main()
