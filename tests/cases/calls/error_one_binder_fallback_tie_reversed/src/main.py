# The tie of error_one_binder_fallback_tie with the variants declared in
# the other order: still ambiguous (docs/LANGUAGE_FEATURES.md "A genuine
# tie in either pass is an Ambiguous overload diagnostic rather than a
# declaration-order pick").
from tpy import dispatch, int32, int64


@dispatch
def cross[T](a: T, b: T, m: int64, n: int32) -> str:
    return "right"


@dispatch
def cross[T](a: T, b: T, m: int32, n: int64) -> str:
    return "left"


def main() -> None:
    print(cross[float](1, 1.5, 1, 1))  # tpyc: error(/Ambiguous overload for 'cross'/)


main()
