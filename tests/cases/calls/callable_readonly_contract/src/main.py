# readonly[...] inside a Callable/Fn param list is the explicit
# non-mutating contract: the C++ signature stays const, and passing a
# container with a live element borrow does NOT warn (inverse of
# warn_callable_borrow_invalidation).
from typing import Callable
from tpy import Fn, int32, readonly


def total(xs: readonly[list[int32]]) -> None:
    print(len(xs))


def use_fn(f: Fn[[readonly[list[int32]]], None]) -> None:
    xs: list[int32] = [1, 2]
    p = xs[0]
    print(p)
    f(xs)  # tpyc: ok


def use_callable(f: Callable[[readonly[list[int32]]], None]) -> None:
    xs: list[int32] = [3, 4]
    p = xs[0]
    print(p)
    f(xs)  # tpyc: ok


def main() -> None:
    use_fn(total)
    use_callable(total)


main()
