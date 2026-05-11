# Per-element ownership check: field access as the literal element
# source. Outside a consuming method, a record field is borrowed and
# can't be moved into an Own[tuple] storage slot. Pairs with the
# in-consuming-method `self.field` early-return at
# check_own_lvalue_into_own's branch 5 (not tested here -- the silent
# acceptance path is harder to capture; this test pins the error path).
from tpy import Int32, Own


class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


class Holder:
    a: P
    b: P
    def __init__(self, a: P, b: P) -> None:
        self.a = a
        self.b = b


def take(t: Own[tuple[P | None, P | None]]) -> Int32:
    return Int32(0)


def helper(h: Holder) -> Int32:
    # h.a / h.b are borrowed field reads -- not movable.
    return take((h.a, h.b))  # tpyc: error(/tuple element 0.*explicit copy/)


def main() -> None:
    h = Holder(P(1), P(2))
    print(helper(h))


main()
