# A diagnostic about an enum method names the enum, never its internal
# companion record.
from enum import Enum
from tpy import int32


class Color(Enum):
    Red = 0

    @staticmethod
    def default() -> "Color":
        return Color.Red


def main() -> None:
    print(Color.default[int32]())  # tpyc: error(/'Color' is not generic/)


main()
