# A module imported under the name of a builtin type, used as an isinstance
# operand: the operand reads the module, which names no type (CPython raises
# TypeError) -- never the builtin `str` by its spelling.
import helper as str


def main() -> None:
    # The operand read that reaches the module.
    print(isinstance("x", str))  # tpyc: error(/'str' is a module here, not a type/)


main()
