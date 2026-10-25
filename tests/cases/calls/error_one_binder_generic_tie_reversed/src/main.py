# The tie of error_one_binder_generic_tie with the variants declared in
# the other order: still ambiguous (docs/LANGUAGE_FEATURES.md "A genuine
# tie in either pass is an Ambiguous overload diagnostic rather than a
# declaration-order pick"). The CPython @dispatch stand-in's first match
# differs by design.
from tpy import dispatch, int32


@dispatch
def pick[T](x: int32, y: T) -> str:
    return "right"


@dispatch
def pick[T](x: T, y: int32) -> str:
    return "left"


def main() -> None:
    x = 1
    # Both take (int32, int32) at T = int32.
    print(pick(x, x))  # tpyc: error(/Ambiguous overload for 'pick'/)


main()
