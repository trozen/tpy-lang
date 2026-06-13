# Args at a callable-value boundary run the same coercion pipeline as
# direct calls: a plain int local (BigInt) narrows into an Int32 callback
# param through both Fn (template) and Callable (std::function), and an
# unannotated list literal resolves against the callback's param context
# (list, not Array).
from typing import Callable
from tpy import Fn, Int32


def cb(x: Int32) -> None:
    print(x)


def take_list(xs: list[Int32]) -> None:
    print(len(xs))


def use_fn(f: Fn[[Int32], None]) -> None:
    n: int = 42
    f(n)


def use_callable(f: Callable[[Int32], None]) -> None:
    n: int = 7
    f(n)


def use_list_fn(f: Fn[[list[Int32]], None]) -> None:
    xs = [1, 2, 3]
    f(xs)


def use_local() -> None:
    f: Callable[[Int32], None] = cb
    n: int = 99
    f(n)


def main() -> None:
    use_fn(cb)
    use_callable(cb)
    use_list_fn(take_list)
    use_local()


main()
