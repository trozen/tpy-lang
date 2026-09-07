# A pointer-slot container global passed at an `Own[container]` slot: that
# parameter is a move sink, not a by-reference bind, so the pass row declines it.
import sys
from tpy import Own


def eat(xs: Own[list[str]]) -> int:
    return len(xs)


def main() -> None:
    print(eat(sys.argv) >= 1)  # tpyc: error(/field\.module_var_type/)


main()
