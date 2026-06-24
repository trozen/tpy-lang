# An @overload-ed generator: the impl carries the `yield`, the stubs declare
# the two callable arms. Both arms are exercised.
from typing import overload, Iterator
from tpy import Int32


@overload
def rep[T](obj: T) -> Iterator[T]: ...
@overload
def rep[T](obj: T, n: Int32) -> Iterator[T]: ...
def rep[T](obj: T, n: Int32 = -1) -> Iterator[T]:
    i = 0
    while n < 0 or i < n:
        yield obj  # tpyc: ok
        i += 1


def main():
    # one-arg arm (unbounded), consumed with a manual break
    count = 0
    for x in rep("hi"):
        print(x)
        count += 1
        if count == 2:
            break
    # two-arg arm (bounded)
    for y in rep(9, 3):
        print(y)


main()
