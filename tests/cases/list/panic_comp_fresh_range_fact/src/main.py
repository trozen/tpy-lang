# A comprehension variable named like a range-proven local is unproven: the
# element's index is checked, and an out-of-range one raises IndexError.
from tpy import int32


def ranged(xs: list[int32], idx: list[int32]) -> None:
    i = 0
    if i < len(xs):
        print("range", [xs[i] for i in idx])  # tpyc: bounds_checked(xs)


def main() -> None:
    ranged([10, 20, 30], [1, 5])


main()
