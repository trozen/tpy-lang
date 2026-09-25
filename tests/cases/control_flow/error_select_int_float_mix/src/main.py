# A ternary over an int arm and a float arm has no one type: CPython keeps
# whichever value it picks (docs/LANGUAGE_FEATURES.md, Conditionals).
from tpy import int32


def main(a: int32, c: bool) -> None:
    # One float type would print the picked int32 as 3.0.
    y = a if c else 2.5  # tpyc: error(/this conditional expression mixes int32 and float, and CPython keeps whichever value it picks; convert to one type: float\(a\) if c else 2\.5, or annotate the target as float$/)
    print(y)


main(3, True)
