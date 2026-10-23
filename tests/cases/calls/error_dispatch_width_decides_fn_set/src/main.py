# A call with a lambda is ranked over the Callable-bearing variants, but
# whether a pending local's width picks the variant is the WHOLE set's fact:
# `take(k: int64, fn: str)` competes at `k` too, so the use fixes the local
# and a later wider store is refused (docs/LANGUAGE_FEATURES.md, "A number
# argument whose local is still open").
from tpy import Fn, dispatch, int32, int64


def wide() -> int64:
    return 7


@dispatch
def take(k: int32, fn: Fn[[int32], int32]) -> int32:
    return fn(k)


@dispatch
def take(k: str, fn: Fn[[str], str]) -> str:
    return fn(k)


@dispatch
def take(k: int64, fn: str) -> str:
    return fn


def main() -> None:
    xs = [1, 2]
    for x in xs:
        print(take(x, lambda z: z))
    # The call above fixed the elements at int32: int32 and int64 compete.
    xs.append(wide())  # tpyc: error(/'xs' holds int32 elements since line 31 \(through 'x', an argument to 'take\(\)'\), and this value is int64; annotate its first binding: xs: list\[int64\] = \[\.\.\.\]/)


main()
