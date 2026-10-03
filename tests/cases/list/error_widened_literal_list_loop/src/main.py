# A loop decides an unannotated list literal's element on the spot; a later
# widening use is refused (docs/LANGUAGE_FEATURES.md "List Literal Inference").
from tpy import int64


def big(v: list[int64]) -> None:
    v.append(5000000000)


def main() -> None:
    ys = [1]
    for v in ys:
        print(v)
    # The loop ran over int32 elements; this parameter would make them int64.
    big(ys)  # tpyc: error(/'ys' holds int32 elements since line \d+ \(a loop iterable\), and it is passed here as list\[int64\].*annotate its first binding: ys: list\[int64\]/)


main()
