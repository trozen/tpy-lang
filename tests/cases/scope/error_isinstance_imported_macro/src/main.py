# A class macro imported under the name of a builtin type, used as an
# isinstance operand: the import names no type (CPython raises TypeError),
# so the operand is no type -- never the builtin `str` by its spelling.
from dataclasses import dataclass as str


def main() -> None:
    # The operand read that reaches the imported macro.
    print(isinstance("x", str))  # tpyc: error(/isinstance\(\) second argument must be a type, got 'str'/)


main()
