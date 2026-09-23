# An unrecognised decorator on an enum method is rejected rather than dropped.
import functools
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    @functools.cache
    def label(self) -> str:  # tpyc: error(/decorator 'cache' is not supported on enum methods/)
        return "x"


def main() -> None:
    print(Color.Red)


main()
