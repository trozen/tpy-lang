# Callable arg passed by keyword (not positional) for an overloaded
# generic function. Per-candidate Fn arg typing must follow the kwarg
# binding to the correct parameter slot.
from typing import overload
from tpy import Fn, Int32


@overload
def reduce[T, U](xs: list[T], func: Fn[[U, T], U], init: U) -> U:  # tpyc: ok
    return init


@overload
def reduce[T](xs: list[T], func: Fn[[T, T], T]) -> T:  # tpyc: ok
    return xs[0]


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    # 3-arg via kwarg: func is at index 1 by name.
    print(reduce(xs, init=Int32(0), func=lambda a, b: a + b))
    # 2-arg via kwarg.
    print(reduce(xs, func=lambda a, b: a + b))


main()
