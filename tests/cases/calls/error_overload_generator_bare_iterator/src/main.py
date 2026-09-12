# An @overload generator impl whose return type is a bare Iterator (no type
# argument) is rejected.
from typing import overload, Iterator
from tpy import int32


@overload
def f[T](obj: T) -> Iterator[T]: ...
@overload
def f[T](obj: T, n: int32) -> Iterator[T]: ...
def f[T](obj: T, n: int32 = -1) -> Iterator:  # tpyc: error(/Iterator.*requires type argument/)
    yield obj


def main():
    for x in f(1):
        print(x)


main()
