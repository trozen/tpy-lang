# Regime C with a TpyLambda whose return-type TPR (U) is unresolvable from
# arity/non-Fn args alone -- formerly the V1 limit, now resolved via a
# per-candidate body trial that pins U from the lambda body. Exercises
# both arities and verifies state cleanup (the lambda body running under
# trial_scope must not leak call edges, mutated params, expr_types,
# borrows, or counters into the enclosing function).
from tpy import Fn, int32, dispatch


@dispatch
def m[T, U](f: Fn[[T], U], xs: list[T]) -> int32:
    return int32(len(xs))


@dispatch
def m[T, U](f: Fn[[T, T], U], xs: list[T]) -> int32:
    return int32(2 * len(xs))


def main() -> None:
    xs: list[int32] = [1, 2, 3]
    # 1-arg lambda -> picks the 1-arg overload; U pinned to int32 from body.
    print(m(lambda a: a + int32(1), xs))
    # 2-arg lambda -> picks the 2-arg overload; U pinned to int32 from body.
    print(m(lambda a, b: a + b, xs))


main()
