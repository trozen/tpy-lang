# A method named like a member: CPython raises TypeError at class creation.
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    def Red(self) -> str:  # tpyc: error(/defined both as a member and as a method/)
        return "x"


def main() -> None:
    print(Color.Red)


main()
