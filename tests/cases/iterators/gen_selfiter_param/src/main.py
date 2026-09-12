# A generator consuming an Iterable[T] whose runtime value is itself a generator
# (a move-only self-iterator) must iterate it without copying it -- a copy is a
# deleted-ctor build error, so compiling + running is the guard.
from typing import Iterator, Iterable
from tpy import int32


# generic generator with a range-loop body -> a move-only resumable frame
def repeat_n[T](obj: T, times: int32) -> Iterator[T]:
    for _ in range(times):
        yield obj


# generator consuming a generic Iterable[T] param (the param's runtime value is
# the move-only generator above) -- resumable (the break forces the frame)
def take[T](it: Iterable[T], n: int32) -> Iterator[T]:
    c: int32 = 0
    for x in it:
        if c >= n:
            break
        yield x
        c += 1


# resumable consumer iterating a TEMPORARY generator source directly
def wrap(n: int32) -> Iterator[int32]:
    yield -1
    for x in repeat_n(5, n):
        yield x


# inverse 1: Iterator[T] param (the `next` strategy -- source IS the iterator)
def take_iter[T](it: Iterator[T], n: int32) -> Iterator[T]:
    c: int32 = 0
    for x in it:
        if c >= n:
            break
        yield x
        c += 1


# inverse 2: a plain list source in a generator (begin_end strategy, unchanged)
def doubled(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        yield x * 2


def main() -> None:
    # Iterable[T] param consuming a move-only generator: for-loop and comprehension
    out: list[int32] = []
    for v in take(repeat_n(7, 5), 3):
        out.append(v)
    print(out)                                       # [7, 7, 7]
    print([v for v in take(repeat_n(8, 5), 2)])      # [8, 8]
    # temporary self-iterator source inside a resumable generator
    print([v for v in wrap(3)])                      # [-1, 5, 5, 5]
    # inverse: Iterator[T] param over a generator
    print([v for v in take_iter(repeat_n(9, 4), 2)])  # [9, 9]
    # inverse: list source
    print([v for v in doubled([1, 2, 3])])           # [2, 4, 6]


main()
