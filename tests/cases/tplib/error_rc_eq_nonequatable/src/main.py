# Regression: tplib.Rc's `def __eq__[T: Equatable](self, other: Rc[T]) -> bool`
# uses the same class-T-shadow pattern as Box, so the class-level T must
# satisfy the method-level bound at every dispatch. The tplib-specific guard
# here pins that the sema enforcement (added in master via bound_check)
# reaches Rc dunders, not just hand-written Box-like classes in user code.
from tpy import Int32
from tplib import Rc


class NotEq:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:
    a = Rc.new(NotEq(Int32(1)))
    b = Rc.new(NotEq(Int32(2)))
    if a == b:  # tpyc: error(/requires type parameter 'T' to satisfy 'Equatable'/)
        print("equal")


main()
