# An or-pattern of int literals over an enum subject. Every alternative is
# validated like a bare literal, so the or form cannot smuggle one past.
from enum import Enum


class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2


def describe(c: Color) -> str:
    match c:
        case 1 | 2:  # tpyc: error(/int literal pattern not valid for subject type 'Color'/)
            return "warm"
        case _:
            return "other"


def main() -> None:
    print(describe(Color.Red))


main()
