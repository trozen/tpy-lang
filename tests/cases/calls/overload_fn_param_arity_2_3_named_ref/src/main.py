# BUGS.md regression: 2-arg / 3-arg generic @dispatch variants with Fn[...] at pos 0,
# called with a named function. Before the fix, the 2-arg form failed with
# "'add' is not a variable" because overload resolution picked the 3-arg
# overload (declared first) as the fn_generic provider but couldn't pin
# its U from a 2-arg call.
from tpy import Fn, Int32, dispatch


@dispatch
def f[T, U](g: Fn[[U, T], U], a: list[T], init: U) -> U:  # tpyc: ok
    return init


@dispatch
def f[T](g: Fn[[T, T], T], a: list[T]) -> T:  # tpyc: ok
    return a[0]


def add(x: Int32, y: Int32) -> Int32:
    return x + y


def main() -> None:
    xs: list[Int32] = [1, 2, 3, 4]
    print(f(add, xs, Int32(0)))  # 3-arg form: returns init = 0
    print(f(add, xs))             # 2-arg form: was the BUGS.md failure


main()
