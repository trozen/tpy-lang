# Two sequential match statements in one scope (and a nested one): the
# subject bindings are numbered, so the C++ locals must not collide.
from enum import Enum


class Color(Enum):
    Red = 1
    Green = 2


class Size(Enum):
    Big = 1
    Small = 2


def main() -> None:
    c = Color.Red
    s = Size.Small
    match c:
        case Color.Red:
            match s:
                case Size.Big:
                    print("red big")
                case Size.Small:
                    print("red small")
        case Color.Green:
            print("green")
    match s:
        case Size.Big:
            print("big")
        case Size.Small:
            print("small")


main()
