# Regression: when an overloaded call appears inside a generic function body
# with a bare TypeParamRef LHS hint (e.g. `r: T = f(...)`), the probe must
# NOT falsely match a non-generic overload via the matcher's param-side TPRef
# branch (which binds-and-accepts unconditionally). Pre-fix, that branch let
# every non-generic candidate pass the LHS-match gate and bias arg analysis
# toward the non-generic overload's params -- e.g. forcing an empty list
# literal to `list[Int32]` even though the user is in a `[T]` scope. Post-fix,
# the non-generic gate skips matching when `lhs_hint` contains a
# `TypeParamRef`, mirroring `_is_useful_seed_binding`'s TPRef filter on the
# generic branch.
from tpy import Int32, Float32, dispatch


@dispatch
def pick(x: Int32, y: Int32) -> Int32:
    return x


@dispatch
def pick[U](x: U, y: U) -> U:
    return x


def use[T](a: T, b: T) -> T:
    # Bare TPRef LHS. Both overloads might appear viable, but the generic
    # `pick[U](U, U) -> U` is the only one that can accept any T. Pre-fix:
    # probe falsely accepts the non-generic candidate `pick(Int32, Int32)`
    # via TPRef binding-and-accepting, biases arg analysis toward Int32,
    # picks the non-generic overload (returns Int32). For non-Int32 T this
    # type-checks fails at the assignment.
    r: T = pick(a, b)
    return r


def main() -> None:
    a: Int32 = use(Int32(1), Int32(2))
    print(a)
    f: Float32 = use(Float32(1.5), Float32(2.5))
    print(f)


main()
