# An isinstance operand naming a nested def of the function: the name is the
# function's local, a function, which names no type (CPython raises
# TypeError) -- never the builtin `bool`.
from tpy import int32


def main() -> None:
    def bool() -> int32:
        return 0
    # The operand reads the nested def.
    print(isinstance(True, bool))  # tpyc: error(/'bool' is a local variable here, not a type/)


main()
