# An EMPTY-seeded list is typed use by use: a read at the default width is
# refused once a parameter widens it (BUGS.md#widened-literal-list-read-truncates).
from tpy import int64


def big(v: list[int64]) -> None:
    v.append(5000000000)


def main() -> None:
    ys = []
    ys.append(1)
    big(ys)
    # The local would be declared int32; the list holds int64.
    n = ys[1]  # tpyc: error(/'ys' holds int64 elements, decided by another use of the list, but this read takes one as int32; annotate its first binding: ys: list\[int64\]/)
    print(n)


main()
