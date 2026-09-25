# Reassigning an int32 name with an int/float `or`: the name is declared,
# so the hint only converts (docs/LANGUAGE_FEATURES.md, numeric tower).
from tpy import int32


def main(a: int32, f: float) -> None:
    y = a
    y = a or f  # tpyc: error(/this `or` mixes int32 and float, and CPython keeps whichever value it picks; convert to one type: float\(a\) or f$/)
    print(y)


main(3, 2.5)
