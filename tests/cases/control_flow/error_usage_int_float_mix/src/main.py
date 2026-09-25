# An unannotated list learning its element type from its uses refuses an
# int and a float (docs/LANGUAGE_FEATURES.md, numeric tower).
from tpy import int32


def main(a: int32) -> None:
    xs = []
    xs.append(a)
    # A float element type would print the int32 as 3.0.
    xs.append(2.5)  # tpyc: error(/list 'xs' mixes int32 and float values, and CPython keeps each value's own type; convert to one type: float\(\.\.\.\) on the int32 values, or annotate the container, e\.g\. xs: list\[float\] = \[\]$/)
    print(xs[0])


main(3)
