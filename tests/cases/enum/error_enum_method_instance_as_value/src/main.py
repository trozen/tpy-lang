# An enum method read off a member without calling it: there is no bound
# method object to take.
from enum import Enum


class Color(Enum):
    Red = 0

    def label(self) -> str:
        return "x"


def main() -> None:
    c = Color.Red
    f = c.label  # tpyc: error(/'label' is a method of enum 'Color'; an enum method cannot be used as a value yet/)
    print(f())


main()
