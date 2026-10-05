# docs/LANGUAGE_FEATURES.md, List Literal Inference: an empty list is seeded by
# its first store or typed container: here a parameter, then a wider store.
from tpy import int32, int64


def wide() -> int64:
    return 1099511627776


def take32(v: list[int32]) -> None:
    print("take32", len(v))


def main() -> None:
    ys = []
    take32(ys)
    # The parameter decided list[int32]; an int64 value does not fit.
    ys.append(wide())  # tpyc: error(/'ys' holds int32 elements since line 16 \(passed as list\[int32\]\), and this value is int64; the list is passed as list\[int32\], so the value must be int32/)
    print(ys)


main()
