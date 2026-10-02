# A typed parameter gives an unannotated list literal a wider element type
# than its literal's default, and an unannotated local takes an element at
# the default width: the read is refused instead of truncating the value
# silently (BUGS.md#widened-literal-list-read-truncates). Reads their
# consumer resolves compile: tests/cases/list/widened_literal_list_reads.
from tpy import int64


def big(v: list[int64]) -> None:
    v.append(5000000000)


def main() -> None:
    ys = [1]
    big(ys)
    # The local would be declared int32; the list holds int64.
    n = ys[1]  # tpyc: error(/'ys' holds int64 elements.*takes one as int32.*annotate its first binding: ys: list\[int64\]/)
    print(n)


main()
