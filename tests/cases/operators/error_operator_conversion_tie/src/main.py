# A record operator answers like its explicit dunder call: both `__add__`
# variants take `right` only by a conversion (a dereference), a tie the call
# rejects as ambiguous (docs/OVERLOAD_DESIGN.md "Operators"), so the operator
# is that error, never a fallback to `OpRight.__radd__`.
from tpy import dispatch, auto_readonly, int32, Deref, Own


class Pt:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class OpLeft:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, p: Pt) -> int32:
        return 10

    @dispatch
    def __add__(self, p: Pt | None) -> int32:
        return 20


class OpRight(Deref[Pt]):
    _p: Pt

    def __init__(self, p: Own[Pt]) -> None:
        self._p = p

    @auto_readonly
    def __deref__(self) -> Pt:
        return self._p

    def __radd__(self, x: OpLeft) -> int32:
        return 30


def main() -> None:
    left = OpLeft()
    right = OpRight(Pt(1))
    # The tie between the two converting variants is the explicit call's error.
    print(left + right)  # tpyc: error(/Ambiguous overload for '__add__'/)


main()
