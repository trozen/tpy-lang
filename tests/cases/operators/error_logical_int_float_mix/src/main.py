# A value-position `or` over an int and a float operand has no one type:
# CPython keeps the operand it picks (docs/LANGUAGE_FEATURES.md, Logical).
from tpy import int32


def main(a: int32, f: float) -> None:
    # As a condition the same `or` only tests each operand, so it compiles.
    if a or f:
        print("truthy")
    # As a value it would need one type for the picked int32 and float.
    y = a or f  # tpyc: error(/this `or` mixes int32 and float, and CPython keeps whichever value it picks; convert to one type: float\(a\) or f, or annotate the target as float$/)
    print(y)


main(3, 2.5)
