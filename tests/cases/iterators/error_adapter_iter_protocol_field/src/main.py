# An iteration adapter conforms only to a protocol whose whole contract it
# meets (docs/LANGUAGE_FEATURES.md "Copy Warnings for Inline Storage"): an
# iterator object at a sub-protocol of Iterable that adds a field is refused.
from typing import Iterable, Protocol
from tpy import int32


class Rows[T](Iterable[T], Protocol):
    tag: int32


def total(rs: Rows[int32]) -> int32:
    t = 0
    for x in rs:
        t += x
    return t


def main() -> None:
    xs = [1, 2, 3]
    print(total(iter(xs)))  # tpyc: error(/Iterator\[int32\] does not conform to protocol Rows\[int32\]/)


main()
