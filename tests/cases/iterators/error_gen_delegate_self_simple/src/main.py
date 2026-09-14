# Self-delegation in a single-yield generator: the frame would embed itself,
# so the emit-order check raises the recursive-delegation diagnostic.
from typing import Iterator
from tpy import int32


def echo() -> Iterator[int32]:  # tpyc: error(/recursive generator delegation/)
    for x in echo():
        yield x


def main() -> None:
    for v in echo():
        print(v)


main()
