# The record an enum's methods compile to is not a name a program can spell.
from enum import Enum


class Color(Enum):
    Red = 0

    def label(self) -> str:
        return "x"


def show(x: __enum_Color) -> None:  # tpyc: error(/Unknown type: __enum_Color/)
    pass


def main() -> None:
    print(Color.Red.label())


main()
