# Self-delegation in a SIMPLE-shaped generator is force-promoted to the
# resumable path so it hits the same clean recursive-delegation diagnostic
# (the lambda capture would otherwise recurse at construction time).
from typing import Iterator
from tpy import Int32


def echo() -> Iterator[Int32]:  # tpyc: error(/recursive generator delegation/)
    for x in echo():
        yield x


def main() -> None:
    for v in echo():
        print(v)


main()
