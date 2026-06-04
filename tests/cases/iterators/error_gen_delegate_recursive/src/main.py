# Recursive generator delegation embeds the generator's own struct by value
# in its frame (infinite size) -- rejected with a clean diagnostic.
from typing import Iterator
from tpy import Int32


def cycle() -> Iterator[Int32]:  # tpyc: error(/recursive generator delegation/)
    yield 0
    for x in cycle():
        yield x


def main() -> None:
    for v in cycle():
        print(v)


main()
