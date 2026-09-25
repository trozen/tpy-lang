# An unannotated dict learning its value type from `d[k] = v` refuses an
# int and a float (docs/LANGUAGE_FEATURES.md, numeric tower).
from tpy import int32


def main(a: int32, f: float) -> None:
    d = {}
    d["k"] = a
    # A float value type would print the int32 as 3.0.
    d["j"] = f  # tpyc: error(/dict 'd' mixes int32 and float values, and CPython keeps each value's own type; convert to one type: float\(\.\.\.\) on the int32 values, or annotate the container, e\.g\. d: dict\[str, float\] = \{\}$/)
    print(d)


main(3, 2.5)
