# `name` / `value` are the intrinsic member properties; a method of that name
# would shadow them (CPython lets the method win, silently).
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    def name(self) -> str:  # tpyc: error(/intrinsic member property/)
        return "x"


def main() -> None:
    print(Color.Red)


main()
