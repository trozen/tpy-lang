# BUGS.md regression: 2-arg / 3-arg generic @overloads with Fn[...] at pos 0,
# called with a lambda. Before the fix, the 2-arg form failed with "lambda
# parameter types cannot be inferred" because the picked fn_generic was
# the 3-arg overload, whose U was unresolvable from a 2-arg call.
from typing import overload
from tpy import Fn, Int32


@overload
def f[T, U](g: Fn[[U, T], U], a: list[T], init: U) -> U:  # tpyc: ok
    return init


@overload
def f[T](g: Fn[[T, T], T], a: list[T]) -> T:  # tpyc: ok
    return a[0]


def main() -> None:
    xs: list[Int32] = [1, 2, 3, 4]
    print(f(lambda a, b: a + b, xs, Int32(0)))  # 3-arg form
    print(f(lambda a, b: a + b, xs))            # 2-arg form: was the BUGS.md failure


main()
