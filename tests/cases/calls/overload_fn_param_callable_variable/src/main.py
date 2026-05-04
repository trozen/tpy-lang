# Callable variable (not a function ref) at a Fn-bearing slot. The
# variable's type is pre-analyzed and feeds the candidate scoring as
# a baseline; the matching candidate's Fn shape determines the
# winner. `Callable` (not `Fn`) is used for local bindings.
from typing import Callable, overload
from tpy import Fn, Int32


@overload
def apply[T](f: Fn[[T], T], x: T) -> T:  # tpyc: ok
    return f(x)


@overload
def apply[T](f: Fn[[T, T], T], x: T) -> T:  # tpyc: ok
    return f(x, x)


def main() -> None:
    # Typed locals holding callables -- pre-analyzed once at the
    # call site, then matched against each candidate's Fn shape.
    f1: Callable[[Int32], Int32] = lambda x: x + Int32(1)
    f2: Callable[[Int32, Int32], Int32] = lambda a, b: a + b
    print(apply(f1, Int32(5)))   # picks 1-param overload -> 6
    print(apply(f2, Int32(5)))   # picks 2-param overload -> 10


main()
