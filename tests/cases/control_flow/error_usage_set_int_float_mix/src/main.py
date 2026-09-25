# An unannotated set learning its element type from `add` refuses an int
# and a float (docs/LANGUAGE_FEATURES.md, numeric tower).
from tpy import int32


def main(a: int32, f: float) -> None:
    s = set()
    s.add(a)
    # A float element type would print the int32 as 3.0.
    s.add(f)  # tpyc: error(/set 's' mixes int32 and float values, and CPython keeps each value's own type; convert to one type: float\(\.\.\.\) on the int32 values, or annotate the container, e\.g\. s: set\[float\] = set\(\)$/)
    print(s)


main(3, 2.5)
