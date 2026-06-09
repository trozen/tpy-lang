# Regression: an Iterator[Own[T]] generator satisfies Iterable[Own[T]]
# (e.g. list()). Conformance must strip Own symmetrically -- on the actual
# side (the iterator's yielded Own[T]) as well as the expected side -- not
# just the expected. Covers value and reference T.
from tpy import Int32, Comparable, Own, copy
from typing import Iterator, Iterable

class Item:
    key: Int32
    tag: Int32
    def __init__(self, key: Int32, tag: Int32) -> None:
        self.key = key
        self.tag = tag
    def __lt__(self, other: 'Item') -> bool:
        return self.key < other.key

def each[T: Comparable](xs: list[T]) -> Iterator[Own[T]]:
    for x in xs:
        yield copy(x)

# A user sink typed Iterable[Own[T]] fed an Iterator[Own[T]] -- the same
# conformance path as list(), beyond the builtin.
def total(xs: Iterable[Own[Int32]]) -> Int32:
    s: Int32 = 0
    for x in xs:
        s += x
    return s

def main() -> None:
    nums: list[Int32] = [3, 1, 2]
    print(list(each(nums)))
    print(total(each(nums)))

    items: list[Item] = [Item(3, 30), Item(1, 10)]
    for it in list(each(items)):
        print(it.key, it.tag)

main()
