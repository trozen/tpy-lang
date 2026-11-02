# A module function named like a builtin type, used as an isinstance operand:
# the operand reads the module's `str`, a function, which names no type
# (CPython raises TypeError) -- never the builtin by its spelling.
from tpy import int32


def str() -> int32:
    return 1


def main() -> None:
    # The operand read that reaches the module function.
    print(isinstance("x", str))  # tpyc: error(/'str' is a module function here, not a type/)


main()
