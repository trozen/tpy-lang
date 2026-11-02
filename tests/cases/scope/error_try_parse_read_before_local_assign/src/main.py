# A try_parse enum operand the function later rebinds to a value: the operand
# reads the function's local before it is bound (CPython's
# UnboundLocalError), never the module enum.
from enum import Enum
from tpy import try_parse


class Color(Enum):
    RED = 1
    GREEN = 2


def main() -> None:
    # The enum read the later `Color = 3` makes local.
    print(try_parse(Color, "RED"))  # tpyc: error(/'Color' is read before it is bound on line 16: .* does not see the module's 'Color'/)
    Color = 3
    print(Color)


main()
