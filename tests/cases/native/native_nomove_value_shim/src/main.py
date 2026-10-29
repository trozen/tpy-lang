# A delegating shim (the operator() of a __call__) hands a by-value @nomove
# native value on by copy, never std::move: its move constructor is deleted.
from tpy import ValueType, nomove, int32
from tpy.extern import native


@nomove
@native("PinnedValue")
class P(ValueType):
    def __init__(self) -> None: ...


class C:
    def __call__(self, p: P) -> int32:
        return 7


def main() -> None:
    p = P()
    c = C()
    print(c(p))  # tpyc: ok


main()
