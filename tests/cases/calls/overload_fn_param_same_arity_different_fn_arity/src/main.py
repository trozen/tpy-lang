# Regime C: same-arity overloads with different Fn shapes. The lambda's
# parameter count picks the matching overload.
from tpy import Fn, Int32, dispatch


@dispatch
def g[T](f: Fn[[T], Int32], xs: list[T]) -> Int32:  # tpyc: ok
    return f(xs[0])


@dispatch
def g[T](f: Fn[[T, T], Int32], xs: list[T]) -> Int32:  # tpyc: ok
    return f(xs[0], xs[0])


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    print(g(lambda a: a + Int32(1), xs))           # picks the 1-param overload
    print(g(lambda a, b: a + b, xs))               # picks the 2-param overload


main()
