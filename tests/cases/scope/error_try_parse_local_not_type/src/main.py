# A try_parse operand naming the function's loop variable: after the loop the
# name is the function's local value, which names no enum (CPython raises
# at the call) -- never the module enum.
from enum import Enum
from tpy import try_parse


class Color(Enum):
    RED = 1


def main() -> None:
    for Color in range(2):
        pass
    # The operand reads the loop's `Color`, an int.
    print(try_parse(Color, "RED"))  # tpyc: error(/'Color' is a local variable here, not a type/)


main()
