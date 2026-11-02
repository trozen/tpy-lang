# A module variable named like a builtin type, used as an isinstance operand:
# the operand reads the module's `str`, an int, which names no type (CPython
# raises TypeError) -- never the builtin by its spelling.
from tpy import int32

str = 17


def main() -> None:
    # The operand read that reaches the module variable.
    print(isinstance("x", str))  # tpyc: error(/'str' is a module variable here, not a type/)


main()
