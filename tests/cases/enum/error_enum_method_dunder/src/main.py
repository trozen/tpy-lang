# A dunder on an enum is rejected: printing, equality and hashing stay the
# enum's intrinsic rules.
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    def __str__(self) -> str:  # tpyc: error(/dunder methods on enums are not supported yet/)
        return "x"


def main() -> None:
    print(Color.Red)


main()
