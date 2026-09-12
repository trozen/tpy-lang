# Regression: when an overloaded call appears inside a generic function body
# with a bare TypeParamRef LHS hint (e.g. `r: T = f(...)`), the probe must
# NOT falsely match a non-generic overload via the matcher's param-side TPRef
# branch (which binds-and-accepts unconditionally). Pre-fix, that branch let
# every non-generic candidate pass the LHS-match gate and bias arg analysis
# toward the non-generic overload's params -- e.g. forcing an empty list
# literal to `list[int32]` even though the user is in a `[T]` scope. Post-fix,
# the non-generic gate skips matching when `lhs_hint` contains a
# `TypeParamRef`, mirroring `_is_useful_seed_binding`'s TPRef filter on the
# generic branch.
from tpy import int32, float32, dispatch


@dispatch
def pick(x: int32, y: int32) -> int32:
    return x


@dispatch
def pick[U](x: U, y: U) -> U:
    return x


def use[T](a: T, b: T) -> T:
    # Bare TPRef LHS. Both overloads might appear viable, but the generic
    # `pick[U](U, U) -> U` is the only one that can accept any T. Pre-fix:
    # probe falsely accepts the non-generic candidate `pick(int32, int32)`
    # via TPRef binding-and-accepting, biases arg analysis toward int32,
    # picks the non-generic overload (returns int32). For non-int32 T this
    # type-checks fails at the assignment.
    r: T = pick(a, b)
    return r


def main() -> None:
    a: int32 = use(int32(1), int32(2))
    print(a)
    f: float32 = use(float32(1.5), float32(2.5))
    print(f)


main()
