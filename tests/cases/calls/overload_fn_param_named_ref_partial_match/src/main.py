# Two same-arity Fn-bearing overloads; a named function whose signature
# matches one but not the other. Regime C's per-candidate dry matcher
# should accept only the matching candidate, leaving a unique winner.
from tpy import Fn, int32, dispatch


@dispatch
def g[T](f: Fn[[T], int32], xs: list[T]) -> int32:  # tpyc: ok
    return f(xs[0])


@dispatch
def g[T](f: Fn[[T, T], int32], xs: list[T]) -> int32:  # tpyc: ok
    return f(xs[0], xs[0])


def square(x: int32) -> int32:
    return x * x


def add(a: int32, b: int32) -> int32:
    return a + b


def main() -> None:
    xs: list[int32] = [3, 4, 5]
    print(g(square, xs))   # picks the 1-param overload -> 9
    print(g(add, xs))      # picks the 2-param overload -> 6


main()
