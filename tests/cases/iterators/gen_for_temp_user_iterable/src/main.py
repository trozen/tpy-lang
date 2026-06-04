# Generators (simple and resumable) iterating a TEMPORARY user iterable via
# the universal __iter__ strategy: the temp must be stored (capture / frame
# field) before __iter__ borrows it, or the iterator dangles.
from typing import Iterator
from tpy import Int32, Own


class Holder:
    items: list[Int32]

    def __init__(self) -> None:
        self.items = [5, 6, 7]

    def __iter__(self) -> Iterator[Int32]:
        for x in self.items:
            yield x


def make() -> Own[Holder]:
    return Holder()


def g_simple() -> Iterator[Int32]:
    for x in make():  # tpyc: ok
        yield x


def g_resumable() -> Iterator[Int32]:
    yield 0
    for x in make():  # tpyc: ok
        yield x


def main() -> None:
    for v in g_simple():
        print(v)
    for v in g_resumable():
        print(v)


main()
