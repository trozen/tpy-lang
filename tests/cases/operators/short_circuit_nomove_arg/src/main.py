# A NON-MOVABLE arg temp in a conditionally-evaluated operand must stay eager.
# The conditional-operand deferral emplaces the temp into a std::optional slot,
# and emplace needs a move ctor that @nomove deletes -- where the eager
# `T t = T(...);` form compiles via guaranteed elision. So these must keep
# building; the ctor running in a skipped branch is a declared divergence the
# compiler warns about at each site, and the Pinned ctor is deliberately
# side-effect-free so CPython parity still holds. The `_hatch` twins bind the
# value to a local first, which says the same thing in the source and silences
# the warning. The `comp_rhs` section is the inverse: a comprehension body
# carries its own region, so nothing in it is built early and no warning fires.
from tpy import Int32, Own, nomove


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
    return flag and take(Pinned(7)) > 0  # tpyc: warning(/builds Pinned even when the branch is not taken/)


def and_rhs_hatch(flag: bool) -> bool:
    # The hatch: the local says the build is unconditional, so no warning.
    p = Pinned(7)  # tpyc: ok
    return flag and take(p) > 0  # tpyc: ok


def or_rhs(flag: bool) -> bool:
    return flag or take(Pinned(8)) > 0  # tpyc: warning(/builds Pinned even when the branch is not taken/)


def or_rhs_hatch(flag: bool) -> bool:
    p = Pinned(8)
    return flag or take(p) > 0  # tpyc: ok


def ternary_arm(flag: bool) -> Int32:
    return take(Pinned(9)) if flag else -1  # tpyc: warning(/builds Pinned even when the branch is not taken/)


def ternary_arm_hatch(flag: bool) -> Int32:
    p = Pinned(9)
    return take(p) if flag else -1  # tpyc: ok


def chained(a: Int32, b: Int32) -> bool:
    # Later comparator of a chained compare: also a conditional operand.
    return a < b < take(Pinned(10))  # tpyc: warning(/builds Pinned even when the branch is not taken/)


def chained_hatch(a: Int32, b: Int32) -> bool:
    p = Pinned(10)
    return a < b < take(p)  # tpyc: ok


def take_ref(p: Pinned) -> Int32:
    """Plain reference param -- a different temp row from the optional one."""
    return p.n


def ref_param(flag: bool) -> bool:
    return flag or take_ref(Pinned(12)) > 0  # tpyc: warning(/builds Pinned even when the branch is not taken/)


def ref_param_hatch(flag: bool) -> bool:
    p = Pinned(12)
    return flag or take_ref(p) > 0  # tpyc: ok


def take_own(p: Own[Pinned]) -> Int32:  # tpyc: warning(/never consumed/)
    """An `Own[T]` slot. The `never consumed` warning is unrelated: `Pinned`
    is `@nomove`, so the param cannot be forwarded or returned anywhere."""
    return p.n


def own_param(flag: bool) -> bool:
    # THE OTHER INVERSE: an `Own[T]` slot binds the rvalue inline, so no temp
    # is created and nothing is built ahead of the guard -- warning here would
    # be a false positive.
    return flag or take_own(Pinned(13)) > 0  # tpyc: ok


@nomove
class Noisy:
    n: Int32

    def __init__(self, n: Int32) -> None:
        # Prints from the CONSTRUCTION itself, so a temp hoisted ahead of the
        # guard would show as a "built" line under the SKIPPED call.
        print("  built", n)
        self.n = n

    def __del__(self) -> None:
        self.n = 0


def use(p: Noisy) -> Int32:
    return p.n


def comp_rhs(flag: bool) -> bool:
    # THE LAZY-BODY INVERSE: the comprehension body carries its own region, so
    # the per-iteration build happens where the comprehension runs -- inside
    # the skipped operand. Warning here would be a false positive, and the
    # remedy it names (bind the value to a local before the expression) is not
    # available to a value built once per iteration.
    return flag or sum([use(Noisy(i)) for i in range(3)]) > 0  # tpyc: ok


def left_operand(flag: bool) -> bool:
    # THE INVERSE: the LEFT operand always evaluates, so the eager build is
    # what the source says and must not warn.
    return take(Pinned(11)) > 0 and flag  # tpyc: ok


def main() -> None:
    print("and", and_rhs(False), and_rhs(True))
    print("and_hatch", and_rhs_hatch(False), and_rhs_hatch(True))
    print("or", or_rhs(True), or_rhs(False))
    print("or_hatch", or_rhs_hatch(True), or_rhs_hatch(False))
    print("ternary", ternary_arm(False), ternary_arm(True))
    print("ternary_hatch", ternary_arm_hatch(False), ternary_arm_hatch(True))
    print("chained", chained(5, 1), chained(0, 5))
    print("chained_hatch", chained_hatch(5, 1), chained_hatch(0, 5))
    print("refparam", ref_param(True), ref_param(False))
    print("refparam_hatch", ref_param_hatch(True), ref_param_hatch(False))
    print("own", own_param(True), own_param(False))
    # Skipped first, then taken: the "built" lines may only follow the second.
    # Bound first because `print("tag", f())` writes the literal to the stream
    # before calling f(), which would interleave differently under CPython.
    comp_skipped = comp_rhs(True)
    print("comp_skipped", comp_skipped)
    comp_taken = comp_rhs(False)
    print("comp_taken", comp_taken)
    print("left", left_operand(True), left_operand(False))


main()
