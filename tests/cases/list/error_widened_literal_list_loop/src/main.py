# A loop variable takes the elements of an unannotated list literal at the
# literal's default width; a later typed parameter widens the list, so the
# loop is refused (BUGS.md#widened-literal-list-read-truncates).
from tpy import int64


def big(v: list[int64]) -> None:
    v.append(5000000000)


def main() -> None:
    ys = [1]
    # `v` is int32 in this loop; the call below makes the list int64.
    for v in ys:  # tpyc: error(/'ys' holds int64 elements.*takes one as int32/)
        print(v)
    big(ys)


main()
