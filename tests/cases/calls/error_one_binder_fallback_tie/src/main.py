# The generic variants a trial commit validates (`cross[float](1, 1.5, ...)`:
# the ranking cannot infer T from an int and a float) tie when each widens
# one argument: ambiguous, never decided by declaration order (sibling:
# _reversed), per docs/LANGUAGE_FEATURES.md "A genuine tie in either pass
# is an Ambiguous overload diagnostic rather than a declaration-order pick".
from tpy import dispatch, int32, int64


@dispatch
def cross[T](a: T, b: T, m: int32, n: int64) -> str:
    return "left"


@dispatch
def cross[T](a: T, b: T, m: int64, n: int32) -> str:
    return "right"


def main() -> None:
    print(cross[float](1, 1.5, 1, 1))  # tpyc: error(/Ambiguous overload for 'cross'/)


main()
