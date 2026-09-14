# A resumable generator delegating to a single-yield callee: the callee's
# frame struct is the `__for_src` field's type like any other generator's.
from typing import Iterator
from tpy import int32


def src() -> Iterator[int32]:
    for i in range(3):
        yield i + 1


def gen() -> Iterator[int32]:
    yield 0
    for x in src():  # tpyc: ok
        yield x


def main() -> None:
    for v in gen():
        print(v)


main()
