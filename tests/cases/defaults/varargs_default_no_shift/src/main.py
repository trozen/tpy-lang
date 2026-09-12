# A skipped fixed positional before *args must keep its own default, not
# receive the keyword-only one.

from tpy import int64


# `b` is a defaulted fixed positional AHEAD of the pack, so C++ cannot spell its
# default (the pack that follows has none) and every call must fill it.
def spread(a: int64, b: int64 = 4, *rest: int64, tag: int64 = 9) -> int64:
    total = a * 1000 + b * 100 + tag
    for r in rest:
        total += r
    return total


def main() -> None:
    print(spread(1))  # b omitted -> 4, not tag's 9: 1409
    print(spread(1, tag=99))  # kwarg present takes the other resolver branch
    print(spread(1, 2, 3, 4, tag=8))  # pack non-empty: no slot to fill


main()
