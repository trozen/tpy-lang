# A simple generator (single yield, lambda peephole) iterating a temporary
# rvalue list: the source is evaluated exactly once -- and lazily, on the
# first pull, not at generator construction (CPython body-deferral timing).
from typing import Iterator
from tpy import Int32, Own


def make() -> Own[list[Int32]]:
    print("making")
    return [10, 20, 30]


def g() -> Iterator[Int32]:
    for x in make():  # tpyc: ok
        yield x


def main() -> None:
    it = g()
    print("created")
    for v in it:
        print(v)


main()
