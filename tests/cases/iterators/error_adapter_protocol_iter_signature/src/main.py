# An iteration adapter conforms only to a protocol whose whole contract it
# meets (docs/LANGUAGE_FEATURES.md "Copy Warnings for Inline Storage"): a
# sub-protocol that redeclares `__iter__` with another signature refuses
# `copy_iter(xs)`, whose `__iter__` takes no argument.
from typing import Iterable, Iterator, Protocol
from tpy import int32, copy_iter


class Rows[T](Iterable[T], Protocol):
    def __iter__(self, extra: int32 = 0) -> Iterator[T]: ...


def total(rs: Rows[int32]) -> int32:
    return 7


def main() -> None:
    xs = [1, 2, 3]
    print(total(copy_iter(xs)))  # tpyc: error(/Iterator\[Own\[int32\]\] does not conform to protocol Rows\[int32\]/)


main()
