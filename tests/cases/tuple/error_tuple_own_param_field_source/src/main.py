# Per-element ownership check: field access as the literal element
# source. Outside a consuming method, a record field is borrowed and
# can't be moved into an Own[tuple] storage slot. Pairs with the
# in-consuming-method `self.field` early-return at
# check_own_lvalue_into_own's branch 5 (not tested here -- the silent
# acceptance path is harder to capture; this test pins the error path).
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import int32, Own


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


class Holder:
    a: P
    b: P
    def __init__(self, a: P, b: P) -> None:
        self.a = a
        self.b = b


def take(t: Own[tuple[P | None, P | None]]) -> int32:
    return int32(0)


def helper(h: Holder) -> int32:
    # h.a / h.b are borrowed field reads -- not movable.
    return take((h.a, h.b))  # tpyc: error(/tuple element 0.*explicit copy/)


def main() -> None:
    h = Holder(P(1), P(2))
    print(helper(h))


main()
