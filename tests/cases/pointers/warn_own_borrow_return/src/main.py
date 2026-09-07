# The plain-PARAMETER source at an `Own[T]` return: a borrowed parameter is
# not the owner, so the owning slot copies and warns -- the lvalue spelling of
# the same rule warn_return_record_borrow_method_own pins for a call. The copy
# is the ACKNOWLEDGED CPython divergence (CPython hands back the caller's Box),
# so the case prints only what both agree on; `good_return` silences it.
from tpy import Int32, Own, copy


class Box:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value


def bad_return(b: Box) -> Own[Box]:
    return b  # tpyc: warning(/copies Box into owned storage/)


def good_return(b: Box) -> Own[Box]:
    return copy(b)  # tpyc: ok


def main() -> None:
    b = Box(1)
    print(bad_return(b).value, good_return(b).value, b.value)


main()
