# A generator taking an Fn (callable) param: the resumable frame deduces the
# callable type as an F_<pname> template arg (like a static-protocol param).
from typing import Iterator
from tpy import Fn, int32


def is_small(n: int32) -> bool:
    return n < 5


def double(n: int32) -> int32:
    return n * 2


# Yield nested in an if, concrete element type, Fn predicate.
def filterfalse(pred: Fn[[int32], bool], it: list[int32]) -> Iterator[int32]:
    for x in it:
        if not pred(x):
            yield x


# Fn + break.
def takewhile(pred: Fn[[int32], bool], it: list[int32]) -> Iterator[int32]:
    for x in it:
        if not pred(x):
            break
        yield x


# Multi-yield Fn generator.
def tag(pred: Fn[[int32], bool], it: list[int32]) -> Iterator[int32]:
    for x in it:
        yield x
        if pred(x):
            yield x * 10


# Fn generator whose yield is a direct loop child -- the same frame at the
# plainest shape.
def transform(fn: Fn[[int32], int32], it: list[int32]) -> Iterator[int32]:
    for x in it:
        yield fn(x)


class Capped:
    cap: int32

    def __init__(self, cap: int32) -> None:
        self.cap = cap

    # Generator method with an Fn param + break, reading a scalar self field.
    def keep(self, pred: Fn[[int32], bool], it: list[int32]) -> Iterator[int32]:
        n: int32 = 0
        for x in it:
            if n >= self.cap:
                break
            if pred(x):
                yield x
                n += 1


def main() -> None:
    nums: list[int32] = [1, 7, 2, 9, 3]
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
    capnums: list[int32] = [1, 2, 3, 4]
    for v in c.keep(is_small, capnums):
        print(v)


main()
