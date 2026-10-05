# docs/LANGUAGE_FEATURES.md, List Literal Inference: an empty list is seeded by
# its first store or typed container: here a typed int8 store, then a wider one.
from tpy import int8, int64


def small() -> int8:
    return 100


def wide() -> int64:
    return 1099511627776


def main() -> None:
    ws = []
    ws.append(small())
    # The int8 first store decided the element; an int64 value does not fit.
    ws.append(wide())  # tpyc: error(/'ws' holds int8 elements \(line 16\), and this value is int64; annotate its first binding: ws: list\[int64\] = \[\]/)
    print(ws)


main()
