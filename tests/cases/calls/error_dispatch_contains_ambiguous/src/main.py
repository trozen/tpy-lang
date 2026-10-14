# An int8 converting equally into `int64 | None` and `int64` `__contains__` is an ambiguous
# `in` test (LANGUAGE_FEATURES.md "Overloading"; CPython runs the first declared).
from tpy import dispatch, int8, int64


class Bag:
    def __init__(self) -> None:
        pass

    @dispatch
    def __contains__(self, x: int64 | None) -> bool:
        return True

    @dispatch
    def __contains__(self, x: int64) -> bool:
        return False


def main() -> None:
    b = Bag()
    n8: int8 = 1
    print(n8 in b)  # tpyc: error(/Ambiguous overload for '__contains__'/)


main()
