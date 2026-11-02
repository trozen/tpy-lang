# A class pattern naming a builtin the function rebinds as a loop variable:
# after the loop the name is the function's local value, which names no type
# (CPython raises TypeError at the pattern).
from tpy import int32


def main(v: str | int32) -> None:
    for str in range(3):
        pass
    match v:
        # The pattern reads the loop's `str`, an int.
        case str():  # tpyc: error(/'str' is a local variable here, not a type/)
            print("str")
        case _:
            print("other")


main("x")
