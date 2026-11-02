# A base Enum has no arithmetic: the enum operator rule keeps rejecting `+`
# now that it leaves membership to the right operand's `__contains__`.
from enum import Enum


class Color(Enum):
    Red = 1


def main() -> None:
    print(Color.Red + 1)  # tpyc: error(/Operator '\+' not supported for enum type 'Color'/)


main()
