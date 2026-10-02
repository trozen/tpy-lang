# Two names for one unannotated list literal, and a typed parameter widens
# it through only one of them: the other name would keep the default
# element width, so the pair is refused at the literal, the line the
# annotation goes on (BUGS.md#widened-literal-list-read-truncates).
from tpy import int64


def big(v: list[int64]) -> None:
    v.append(5000000000)


def main() -> None:
    # `zs` below is the same list; only `ys` meets the int64 parameter.
    ys = [1]  # tpyc: error(/'zs' and 'ys' name one list.*int64 elements through 'ys' and int32 through 'zs'.*ys: list\[int64\]/)
    zs = ys
    big(ys)
    print(len(zs))


main()
