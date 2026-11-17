# An iteration adapter conforms only to a protocol whose whole contract it
# meets (docs/LANGUAGE_FEATURES.md "Copy Warnings for Inline Storage"):
# `copy_iter(xs)` offers Iterable's contract, so a sub-protocol of Iterator
# (which steps with `__next__`) refuses it.
from typing import Iterator, Protocol
from tpy import int32, copy_iter


class Steps[T](Iterator[T], Protocol):
    pass


def total(s: Steps[int32]) -> int32:
    t = 0
    for x in s:
        t += x
    return t


def main() -> None:
    xs = [1, 2, 3]
    print(total(copy_iter(xs)))  # tpyc: error(/CopyIter\[int32\] does not conform to protocol Steps\[int32\]/)


main()
