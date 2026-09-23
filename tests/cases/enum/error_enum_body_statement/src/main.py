# An enum body takes member assignments and methods; any other statement
# is rejected with the rule named.
from enum import Enum
from tpy import int32


class Color(Enum):
    Red = 0
    Blue = 1

    count: int32 = 0  # tpyc: error(/Enum body must contain only member assignments .* and methods/)


def main() -> None:
    print(Color.Red)


main()
