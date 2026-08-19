# A NON-MOVABLE arg temp in a conditionally-evaluated operand must stay eager.
# The conditional-operand deferral emplaces the temp into a std::optional slot,
# and emplace needs a move ctor that @nomove deletes -- where the eager
# `T t = T(...);` form compiles via guaranteed elision. So these must keep
# building; the ctor running in a skipped branch is the accepted cost, and the
# Pinned ctor is deliberately side-effect-free so CPython parity still holds.
from tpy import Int32, nomove


@nomove
class Pinned:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __del__(self) -> None:
        # A __del__ is what makes codegen emit the deleted move ctor at all;
        # without it the record keeps an implicit (usable) move.
        self.n = 0


def take(p: Pinned | None) -> Int32:
    """Reference param, so a `Pinned(...)` rvalue argument needs a hoisted temp."""
    return p.n if p is not None else 0


def and_rhs(flag: bool) -> bool:
    # The `and` RHS temp cannot defer -- Pinned has no move ctor.
    return flag and take(Pinned(7)) > 0


def or_rhs(flag: bool) -> bool:
    return flag or take(Pinned(8)) > 0


def ternary_arm(flag: bool) -> Int32:
    return take(Pinned(9)) if flag else -1


def chained(a: Int32, b: Int32) -> bool:
    # Later comparator of a chained compare: also a conditional operand.
    return a < b < take(Pinned(10))


def main() -> None:
    print("and", and_rhs(False), and_rhs(True))
    print("or", or_rhs(True), or_rhs(False))
    print("ternary", ternary_arm(False), ternary_arm(True))
    print("chained", chained(5, 1), chained(0, 5))


main()
