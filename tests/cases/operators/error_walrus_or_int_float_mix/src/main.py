# A walrus binds the value of an int/float `or`, so the `or` is no truth
# test and has no one type (docs/LANGUAGE_FEATURES.md, numeric tower).
from tpy import int32


def main(a: int32, f: float) -> None:
    # `y` would need one type for the picked int32 and float.
    if (y := a or f):  # tpyc: error(/this `or` mixes int32 and float, and CPython keeps whichever value it picks; convert to one type: float\(a\) or f$/)
        print(y)


main(3, 2.5)
