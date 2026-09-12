# A resumable generator delegating to a SIMPLE-shaped callee: the callee is
# forced off the lambda peephole into a named struct so its type can be a
# __for_src frame field.
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
