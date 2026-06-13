# A generator factory with default arg values is callable with the arg omitted
# -- across the simple-peephole, resumable, generic, method, and Final paths.
from typing import Iterator, Iterable, Final
from tpy import Int32


DEFAULT_STOP: Final[Int32] = 4


# Simple generator (single yield in a tail while-loop), two literal defaults.
def upto(stop: Int32 = 3, step: Int32 = 1) -> Iterator[Int32]:
    i: Int32 = 0
    while i < stop:
        yield i
        i += step


# Default referencing a module-level Final constant.
def upto_final(stop: Int32 = DEFAULT_STOP) -> Iterator[Int32]:
    i: Int32 = 0
    while i < stop:
        yield i
        i += 1


# Resumable generator (two yields), one default.
def bounded(limit: Int32 = 2) -> Iterator[Int32]:
    yield 0
    i: Int32 = 1
    while i <= limit:
        yield i
        i += 1


# Generic generator (protocol param) with a default -- the proto-param
# default-threading path, resumable via the break.
def head[T](it: Iterable[T], n: Int32 = 2) -> Iterator[T]:
    c: Int32 = 0
    for x in it:
        if c >= n:
            break
        yield x
        c += 1


class Box:
    # Simple generator METHOD with a default (the record_name peephole path).
    def upto_m(self, stop: Int32 = 2) -> Iterator[Int32]:
        i: Int32 = 0
        while i < stop:
            yield i
            i += 1


def main() -> None:
    for v in upto():
        print(v)
    print("--")
    for v in upto(5):
        print(v)
    print("--")
    for v in upto(6, 2):
        print(v)
    print("--")
    for v in upto_final():
        print(v)
    print("--")
    for v in bounded():
        print(v)
    print("--")
    for v in bounded(1):
        print(v)
    print("--")
    nums: list[Int32] = [10, 20, 30, 40]
    for v in head(nums):
        print(v)
    print("--")
    for v in head(nums, 3):
        print(v)
    print("--")
    b = Box()
    for v in b.upto_m():
        print(v)
    print("--")
    for v in b.upto_m(3):
        print(v)


main()
