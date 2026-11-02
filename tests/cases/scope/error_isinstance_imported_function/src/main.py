# An imported function named like a builtin type, used as an isinstance
# operand: the operand reads the imported function, which names no type
# (CPython raises TypeError) -- never the builtin `str` by its spelling.
from helper import str


def main() -> None:
    # The operand read that reaches the imported function.
    print(isinstance("x", str))  # tpyc: error(/'str' is a module function here, not a type/)


main()
