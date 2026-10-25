# An operator dunder set that ties at the top for its operand is the call's
# ambiguity error (docs/OVERLOAD_DESIGN.md "Ambiguity"); CPython's
# `@dispatch` stand-in would run the first variant.
from tpy import dispatch, int32


class V:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, x: int | None) -> int32:
        return 1

    @dispatch
    def __add__(self, x: int) -> int32:
        return 2


def main() -> None:
    v = V()
    n: int = 5
    # `int` at `int | None` and at `int` match equally.
    print(v + n)  # tpyc: error(/Ambiguous overload for '__add__': multiple candidates match equally:$/)


main()
