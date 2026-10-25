# Two generic variants whose substitutions coincide tie: an ambiguous call,
# never a declaration-order pick (sibling: _reversed). The CPython @dispatch
# stand-in runs the first match, so its pick differs by design.
from tpy import dispatch, int32


@dispatch
def pick[T](x: T, y: int32) -> str:
    return "left"


@dispatch
def pick[T](x: int32, y: T) -> str:
    return "right"


def main() -> None:
    x = 1
    # Both take (int32, int32) at T = int32.
    print(pick(x, x))  # tpyc: error(/Ambiguous overload for 'pick'/)


main()
