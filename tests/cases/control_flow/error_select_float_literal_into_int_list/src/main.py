# A float literal list beside an int list shares no element type: CPython
# keeps the list it picks (docs/LANGUAGE_FEATURES.md, numeric tower).
from tpy import int32


def main(c: bool) -> None:
    xs: list[int32] = [1]
    # `[2.5]` cannot become a `list[int32]`, nor `xs` a `list[float]`.
    y = xs if c else [2.5]  # tpyc: error(/this conditional expression mixes int32 and float elements, and CPython keeps whichever value it picks; declare xs as list\[float\]$/)
    print(y)


main(False)
