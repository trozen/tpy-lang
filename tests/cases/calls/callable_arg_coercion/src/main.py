# Args at a callable-value boundary run the same coercion pipeline as
# direct calls: a plain int local (BigInt) narrows into an int32 callback
# param through both Fn (template) and Callable (std::function), and an
# unannotated list literal resolves against the callback's param context
# (list, not Array).
from typing import Callable
from tpy import Fn, int32


def cb(x: int32) -> None:
    print(x)


def take_list(xs: list[int32]) -> None:
    print(len(xs))


def use_fn(f: Fn[[int32], None]) -> None:
    n: int = 42
    f(n)


def use_callable(f: Callable[[int32], None]) -> None:
    n: int = 7
    f(n)


def use_list_fn(f: Fn[[list[int32]], None]) -> None:
    xs = [1, 2, 3]
    f(xs)


def use_local() -> None:
    f: Callable[[int32], None] = cb
    n: int = 99
    f(n)


def main() -> None:
    use_fn(cb)
    use_callable(cb)
    use_list_fn(take_list)
    use_local()


main()
