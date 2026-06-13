# A generator taking an Fn (callable) param: the resumable frame deduces the
# callable type as an F_<pname> template arg (like a static-protocol param).
from typing import Iterator
from tpy import Fn, Int32


def is_small(n: Int32) -> bool:
    return n < 5


def double(n: Int32) -> Int32:
    return n * 2


# Resumable (yield nested in if), concrete element type, Fn predicate.
def filterfalse(pred: Fn[[Int32], bool], it: list[Int32]) -> Iterator[Int32]:
    for x in it:
        if not pred(x):
            yield x


# Resumable (Fn + break).
def takewhile(pred: Fn[[Int32], bool], it: list[Int32]) -> Iterator[Int32]:
    for x in it:
        if not pred(x):
            break
        yield x


# Multi-yield Fn generator (forces resumable distinctly from break/if).
def tag(pred: Fn[[Int32], bool], it: list[Int32]) -> Iterator[Int32]:
    for x in it:
        yield x
        if pred(x):
            yield x * 10


# Simple-peephole Fn generator (yield is a direct child) -- the inverse:
# this path already worked and must keep working.
def transform(fn: Fn[[Int32], Int32], it: list[Int32]) -> Iterator[Int32]:
    for x in it:
        yield fn(x)


class Capped:
    cap: Int32

    def __init__(self, cap: Int32) -> None:
        self.cap = cap

    # Generator method with an Fn param + break, reading a scalar self field.
    def keep(self, pred: Fn[[Int32], bool], it: list[Int32]) -> Iterator[Int32]:
        n: Int32 = 0
        for x in it:
            if n >= self.cap:
                break
            if pred(x):
                yield x
                n += 1


def main() -> None:
    nums: list[Int32] = [1, 7, 2, 9, 3]
    for v in filterfalse(is_small, nums):
        print(v)
    print("--")
    for v in takewhile(is_small, [1, 2, 3, 7, 4]):
        print(v)
    print("--")
    for v in tag(is_small, [1, 9]):
        print(v)
    print("--")
    for v in transform(double, [1, 2, 3]):
        print(v)
    print("--")
    c = Capped(2)
    # Bind the list to a local: a generator METHOD captures a reference-type
    # param by reference in its frame, so a literal (rvalue) arg can't bind.
    capnums: list[Int32] = [1, 2, 3, 4]
    for v in c.keep(is_small, capnums):
        print(v)


main()
