# Regime C body trial with capture of an outer variable. The trial scope
# must roll back any borrow-tracker entries / call edges / capture marks
# that the body analysis registered against the enclosing function;
# otherwise spurious entries would alter codegen for the enclosing main()
# (e.g. `xs` getting flagged as mutated when it isn't).
from tpy import Fn, Int32, dispatch


@dispatch
def m[T, U](f: Fn[[T], U], xs: list[T]) -> Int32:
    return Int32(len(xs))


@dispatch
def m[T, U](f: Fn[[T, T], U], xs: list[T]) -> Int32:
    return Int32(2 * len(xs))


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    bias: Int32 = 10
    # Lambda captures `bias` -- both candidate trials run body analysis
    # which registers `bias` as captured / typed. After Regime C picks
    # the 1-arg candidate, the post-Regime-C re-analysis sets up the
    # final capture state; trials must not have polluted it.
    print(m(lambda a: a + bias, xs))


main()
