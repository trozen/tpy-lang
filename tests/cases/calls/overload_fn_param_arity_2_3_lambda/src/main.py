# BUGS.md regression: 2-arg / 3-arg generic @dispatch variants with Fn[...] at pos 0,
# called with a lambda. Before the fix, the 2-arg form failed with "lambda
# parameter types cannot be inferred" because the picked fn_generic was
# the 3-arg overload, whose U was unresolvable from a 2-arg call.
from tpy import Fn, int32, dispatch


@dispatch
def f[T, U](g: Fn[[U, T], U], a: list[T], init: U) -> U:  # tpyc: ok
    return init


@dispatch
def f[T](g: Fn[[T, T], T], a: list[T]) -> T:  # tpyc: ok
    return a[0]


def main() -> None:
    xs: list[int32] = [1, 2, 3, 4]
    print(f(lambda a, b: a + b, xs, int32(0)))  # 3-arg form
    print(f(lambda a, b: a + b, xs))            # 2-arg form: was the BUGS.md failure


main()
