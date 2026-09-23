# An enum method read through the type as a value is rejected with its own
# message rather than "no member".
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    def label(self) -> str:
        return "x"


def main() -> None:
    f = Color.label  # tpyc: error(/is a method; an enum method cannot be used as a value yet/)
    print(f(Color.Red))


main()
