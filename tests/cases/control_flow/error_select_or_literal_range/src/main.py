# An and/or literal operand must fit the joined fixed-width type, as a
# ternary arm must (docs/LANGUAGE_FEATURES.md, Logical).
from tpy import uint8


def main(u: uint8) -> None:  # a parameter: a bound-once `u = 0` decides `or`
    x = u or 300  # tpyc: error(/Integer literal 300 is outside uint8 range/)
    print(x)


main(0)
