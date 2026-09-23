# Per-element ownership check: subscript expression as the literal
# element source. Subscript isn't a TpyName, so the is_auto_moved
# fast-path (which gates on TpyName + last_use) must correctly fall
# through to the borrowed-source diagnostic rather than short-circuit.
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import int32, Own


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def take(t: Own[tuple[P | None, P | None]]) -> int32:
    return int32(0)


def helper(items: list[P]) -> int32:
    # items[0] / items[1] are borrowed slot reads -- not movable into
    # the Own[tuple] storage form.
    return take((items[0], items[1]))  # tpyc: error(/tuple element 0.*explicit copy/)


def main() -> None:
    xs: list[P] = [P(1), P(2)]
    print(helper(xs))


main()
