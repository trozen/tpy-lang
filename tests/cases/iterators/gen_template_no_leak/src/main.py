# Multiple generators in one module: a generic / protocol-param generator must
# not leak its template header onto a later generator that reuses a param name.
from typing import Iterator, Iterable
from tpy import Fn, int32


def is_small(n: int32) -> bool:
    return n < 5


# Generic protocol-param generator, param named `it`.
def skip_first[T](it: Iterable[T]) -> Iterator[T]:
    started = False
    for x in it:
        if started:
            yield x
        started = True


# The intersection of both fixes: a generator with a generic type param, a
# protocol param (`it`), AND an Fn param (`pred`) -- the real itertools shape.
# Also exercises the protocol-args-then-Fn-args template-arg ordering, and
# (being followed by other generators reusing `it`/`pred`) the no-leak path.
def gtakewhile[T](pred: Fn[[T], bool], it: Iterable[T]) -> Iterator[T]:
    for x in it:
        if not pred(x):
            break
        yield x


# Concrete generator emitted AFTER the generics, reusing the param name `it`
# -- this is what previously inherited the prior generator's template header.
def tag(it: list[int32]) -> Iterator[int32]:
    for x in it:
        yield x
        yield x * 10


class Doubler:
    # Generator METHOD after the generics (the leak also crossed the
    # free-fn/method boundary). Same param name `it`.
    def each_twice(self, it: list[int32]) -> Iterator[int32]:
        for x in it:
            yield x
            yield x


# Another generic after the concrete ones -- the reverse order, also clean.
def first_n[T](it: Iterable[T], n: int32) -> Iterator[T]:
    c: int32 = 0
    for x in it:
        if c >= n:
            break
        yield x
        c += 1


def main() -> None:
    for v in skip_first([1, 2, 3]):
        print(v)
    print("--")
    gnums: list[int32] = [1, 2, 7, 3]
    for v in gtakewhile(is_small, gnums):
        print(v)
    print("--")
    for v in tag([4, 5]):
        print(v)
    print("--")
    d = Doubler()
    mnums: list[int32] = [7, 8]
    for v in d.each_twice(mnums):
        print(v)
    print("--")
    for v in first_n([9, 8, 7, 6], 2):
        print(v)


main()
