# A default naming the enclosing enum is a NameError at class-body time in
# CPython, so it is rejected (a bare member name is rejected too, stricter).
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    def f(self, o: "Color" = Color.Red) -> str:  # tpyc: error(/default value cannot name 'Color'/)
        return "x"


def main() -> None:
    print(Color.Red)


main()
