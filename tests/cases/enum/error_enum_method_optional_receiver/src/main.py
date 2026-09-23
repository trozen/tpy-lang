# A method call on an `Optional[Color]` receiver takes the value-type rule
# every Optional value type has: narrow first (`if c is not None:`).
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    def label(self) -> str:
        return "x"


def show(c: Color | None) -> None:
    print(c.label())  # tpyc: error(/Cannot call method 'label' on type Color \| None/)


def main() -> None:
    show(Color.Red)


main()
