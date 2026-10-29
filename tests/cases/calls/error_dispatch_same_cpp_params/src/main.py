# Two method variants whose C++ parameters coincide (`str` and `StrView` are
# both a view) with the same receiver const-ness are refused in sema
# (docs/LANGUAGE_FEATURES.md "Overloading": refused as identical parameters).
from tpy import dispatch, int32, StrView


class C:
    def __init__(self) -> None:
        pass

    @dispatch
    def m(self, x: str) -> int32:
        return 1

    @dispatch
    def m(self, x: StrView) -> int32:  # tpyc: error(/@dispatch variants of 'm' have identical parameter types/)
        return 2


def main() -> None:
    print(C().m("a"))


main()
