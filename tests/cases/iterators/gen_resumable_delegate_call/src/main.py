# A resumable (multi-yield) generator delegating to a sub-generator call:
# the sub-iterator lives in a __for_src frame field typed as the callee's
# struct, advanced in place across suspensions.
from typing import Iterator
from tpy import Int32


def src() -> Iterator[Int32]:
    yield 1
    yield 2


def gen() -> Iterator[Int32]:
    yield 0
    for x in src():  # tpyc: ok
        yield x
    yield 9


def main() -> None:
    for v in gen():
        print(v)


main()
