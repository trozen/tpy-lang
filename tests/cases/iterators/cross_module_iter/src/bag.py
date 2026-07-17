# A generic iterable whose __iter__ yields its own type parameter T. Iterating
# an instance of this needs T bound from the instance's type args -- the path
# that regressed when the type was reached cross-module without importing it.
# @nocopy so a silent copy at the cross-module `make_bag` return boundary is a
# compile error, not an invisible parity divergence (per CLAUDE.md's
# reference-type happy-test rule).
from tpy import Own, nocopy
from typing import Iterator


@nocopy
class Bag[T]:
    _items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self._items = items

    def __iter__(self) -> Iterator[T]:
        for x in self._items:
            yield x


def make_bag[T](items: Own[list[T]]) -> Own[Bag[T]]:
    return Bag[T](items)
