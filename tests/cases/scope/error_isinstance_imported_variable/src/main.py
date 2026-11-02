# An imported variable named like a builtin type, used as an isinstance
# operand: the operand reads the imported variable, which names no type
# (CPython raises TypeError) -- never the builtin `str` by its spelling.
from helper import str


def main() -> None:
    # The operand read that reaches the imported variable.
    print(isinstance("x", str))  # tpyc: error(/'str' is a module variable here, not a type/)


main()
