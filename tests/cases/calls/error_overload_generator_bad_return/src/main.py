# An @overload generator impl whose return type is not Iterator[T] is rejected
# (the impl skips register_function, so the check runs in _set_generator_yield_type).
from typing import overload, Iterator
from tpy import Int32


@overload
def f[T](obj: T) -> Iterator[T]: ...
@overload
def f[T](obj: T, n: Int32) -> Iterator[T]: ...
def f[T](obj: T, n: Int32 = -1) -> list[T]:  # tpyc: error(/must have return type 'Iterator/)
    yield obj


def main():
    for x in f(1):
        print(x)


main()
