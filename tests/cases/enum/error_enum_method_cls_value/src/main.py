# `cls` in an enum classmethod names the enum, never a value: the classmethod
# rule applies to the enum alias exactly as to a record.
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    @classmethod
    def me(cls) -> "Color":
        x = cls  # tpyc: error(/'cls' can only be used to construct/)
        return Color.Red


def main() -> None:
    print(Color.me())


main()
