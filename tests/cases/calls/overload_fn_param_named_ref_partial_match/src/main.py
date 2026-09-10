# Two same-arity Fn-bearing overloads; a named function whose signature
# matches one but not the other. Regime C's per-candidate dry matcher
# should accept only the matching candidate, leaving a unique winner.
from tpy import Fn, Int32, dispatch


@dispatch
def g[T](f: Fn[[T], Int32], xs: list[T]) -> Int32:  # tpyc: ok
    return f(xs[0])


@dispatch
def g[T](f: Fn[[T, T], Int32], xs: list[T]) -> Int32:  # tpyc: ok
    return f(xs[0], xs[0])


def square(x: Int32) -> Int32:
    return x * x


def add(a: Int32, b: Int32) -> Int32:
    return a + b


def main() -> None:
    xs: list[Int32] = [3, 4, 5]
    print(g(square, xs))   # picks the 1-param overload -> 9
    print(g(add, xs))      # picks the 2-param overload -> 6


main()
