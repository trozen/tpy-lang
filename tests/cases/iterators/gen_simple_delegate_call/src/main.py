# A simple generator delegating to another generator via a call expression:
# the sub-iterator must be captured once -- re-evaluating the call per pull
# would restart it.
from typing import Iterator
from tpy import Int32


def src() -> Iterator[Int32]:
    yield 1
    yield 2


def g() -> Iterator[Int32]:
    for x in src():  # tpyc: ok
        yield x


def main() -> None:
    for v in g():
        print(v)


main()
