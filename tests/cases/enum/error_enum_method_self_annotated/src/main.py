# `self` on an enum method is the member; an annotation has nothing to add.
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    def g(self: "Color") -> str:  # tpyc: error(/'self' is the enum member and cannot be annotated/)
        return "x"


def main() -> None:
    print(Color.Red)


main()
