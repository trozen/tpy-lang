# An enum value on the RIGHT of `in` is no container: the membership path
# refuses it, as it does any non-iterable.
from enum import Enum


class Color(Enum):
    Red = 1


def main() -> None:
    print(1 in Color.Red)  # tpyc: error(/Cannot use 'in' with non-iterable type Color/)


main()
