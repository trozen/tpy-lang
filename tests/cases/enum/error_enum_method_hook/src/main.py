# An Enum hook (`_missing_` and friends) is a sunder, not a dunder, and would
# be accepted-then-never-called: `Color(99)` panics where CPython consults it.
from enum import Enum
from tpy import int32


class Color(Enum):
    Red = 0
    Blue = 1

    @classmethod
    def _missing_(cls, v: int32) -> "Color":  # tpyc: error(/Enum hooks .* are not supported yet/)
        return Color.Red


def main() -> None:
    print(Color.Red)


main()
