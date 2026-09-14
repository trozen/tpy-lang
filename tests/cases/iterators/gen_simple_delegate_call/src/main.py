# A single-yield generator delegating to another generator via a call
# expression: the sub-iterator must be held once in the frame -- re-evaluating
# the call per pull would restart it.
from typing import Iterator
from tpy import int32


def src() -> Iterator[int32]:
    yield 1
    yield 2


def g() -> Iterator[int32]:
    for x in src():  # tpyc: ok
        yield x


def main() -> None:
    for v in g():
        print(v)


main()
