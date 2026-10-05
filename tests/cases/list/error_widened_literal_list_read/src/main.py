# docs/LANGUAGE_FEATURES.md, List Literal Inference: an empty list is seeded by
# its first store or typed container: here a store, sorted(), a wider store.
from tpy import int64


def wide() -> int64:
    return 1099511627776


def main() -> None:
    ys = []
    ys.append(1)
    # sorted() decides the element as int32 here.
    print(sorted(ys))
    ys.append(wide())  # tpyc: error(/'ys' holds int32 elements since line 14 \(passed as Iterable\[int32\]\), and this value is int64; annotate its first binding: ys: list\[int64\] = \[\]/)
    print(ys)


main()
