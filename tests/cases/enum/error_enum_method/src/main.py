# Methods on enums are not supported yet, and the diagnostic names that rule
# rather than the AST node kind -- `Color.from_str` / `c.label()` is the shape
# users reach for, so the message has to say what to do instead.
from enum import Enum


class Color(Enum):
    Red = 1
    Blue = 2

    def is_warm(self) -> bool:  # tpyc: error(/Methods on enums are not supported yet/)
        return self == Color.Red


def main() -> None:
    print(Color.Red)


main()
