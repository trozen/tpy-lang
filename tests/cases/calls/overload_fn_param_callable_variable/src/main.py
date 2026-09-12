# Callable variable (not a function ref) at a Fn-bearing slot. The
# variable's type is pre-analyzed and feeds the candidate scoring as
# a baseline; the matching candidate's Fn shape determines the
# winner. `Callable` (not `Fn`) is used for local bindings.
from typing import Callable
from tpy import Fn, int32, dispatch


@dispatch
def apply[T](f: Fn[[T], T], x: T) -> T:  # tpyc: ok
    return f(x)


@dispatch
def apply[T](f: Fn[[T, T], T], x: T) -> T:  # tpyc: ok
    return f(x, x)


def main() -> None:
    # Typed locals holding callables -- pre-analyzed once at the
    # call site, then matched against each candidate's Fn shape.
    f1: Callable[[int32], int32] = lambda x: x + int32(1)
    f2: Callable[[int32, int32], int32] = lambda a, b: a + b
    print(apply(f1, int32(5)))   # picks 1-param overload -> 6
    print(apply(f2, int32(5)))   # picks 2-param overload -> 10


main()
