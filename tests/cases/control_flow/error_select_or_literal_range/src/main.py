# An and/or literal operand must fit the joined fixed-width type, as a
# ternary arm must (docs/LANGUAGE_FEATURES.md, Logical).
from tpy import uint8


def main() -> None:
    u: uint8 = 0
    x = u or 300  # tpyc: error(/Integer literal 300 is outside uint8 range/)
    print(x)


main()
