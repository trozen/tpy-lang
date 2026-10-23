# Where a pending local's width picks the variant (take(int32) / take(int64)),
# the use fixes the local, so a later wider store is refused (the rule:
# docs/LANGUAGE_FEATURES.md, "A number argument whose local is still open").
from tpy import dispatch, int32, int64


def wide() -> int64:
    return 7


@dispatch
def take(k: int32) -> str:
    return "i32"


@dispatch
def take(k: int64) -> str:
    return "i64"


def main() -> None:
    xs = [1, 2]
    for x in xs:
        print(take(x))
    # The call above fixed the elements at int32: its variants differ in width.
    xs.append(wide())  # tpyc: error(/'xs' holds int32 elements since line 24 \(through 'x', an argument to 'take\(\)'\), and this value is int64; annotate its first binding: xs: list\[int64\] = \[\.\.\.\]/)


main()
