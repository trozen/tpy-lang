# Per-element ownership check at an Own[tuple[T | None, ...]] call site:
# borrowed lvalues at non-last-use are rejected with a copy() hint. Mirrors
# the existing return-path check in error_tuple_own_outer_no_copy.
from tpy import int32, Own


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def take(t: Own[tuple[P | None, P | None]]) -> int32:
    return int32(0)


def main() -> None:
    a = P(1)
    b = P(2)
    # Both `a` and `b` are reused below, so neither is at last use here.
    # _check_own_tuple_literal_arg raises on element 0 immediately, so
    # element 1's parallel diagnostic is masked -- update the annotation
    # if a future "collect all element errors" pass lands.
    print(take((a, b)))  # tpyc: error(/tuple element 0.*explicit copy/)
    print(take((a, b)))  # last-use, no error


main()
