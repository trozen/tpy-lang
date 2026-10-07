# No candidate may narrow the int32 this local holds (LANGUAGE_FEATURES "List
# Literal Inference"); the message names the list at the type it holds.
from tpy import dispatch, int8, int16


@dispatch
def f(xs: list[int8]) -> str:
    return "8"


@dispatch
def f(xs: list[int16]) -> str:
    return "16"


def main() -> None:
    ms = [1, 2]
    print(f(ms))  # tpyc: error(/No matching overload for f\(list\[int32\]\)/)


main()
