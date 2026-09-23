# Per-element ownership check: element 0 is a fresh rvalue (movable);
# element 1 is a borrowed lvalue. The diagnostic fires for element 1,
# proving the per-element check continues past element 0 when element 0
# is OK. (The first-error-wins behaviour is otherwise documented in the
# sibling error_tuple_optional_own_param_borrowed test.)
# TO BE FIXED: the scalar twin WARNS and copies here; this error becomes that
# warning (BUGS.md#borrowed-tuple-at-own-call-arg, plan unit U3 D1).
from tpy import int32, Own


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def take(t: Own[tuple[P | None, P | None]]) -> int32:
    return int32(0)


def main() -> None:
    keep = P(7)
    # Element 0 is a fresh constructor (rvalue, naturally movable); element
    # 1 is `keep` which is reused below.
    print(take((P(99), keep)))  # tpyc: error(/tuple element 1.*explicit copy/)
    print(keep.x)  # ensures `keep` isn't at last use above


main()
