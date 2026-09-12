# Regression: an Iterator[Own[T]] generator satisfies Iterable[Own[T]]
# (e.g. list()). Conformance must strip Own symmetrically -- on the actual
# side (the iterator's yielded Own[T]) as well as the expected side -- not
# just the expected. Covers value and reference T. copy() makes owned
# copies, so copy semantics are intended in every section here.
from tpy import int32, Comparable, Own, copy
from typing import Iterator, Iterable

class Item:
    key: int32
    tag: int32
    def __init__(self, key: int32, tag: int32) -> None:
        self.key = key
        self.tag = tag
    def __lt__(self, other: 'Item') -> bool:
        return self.key < other.key

def each[T: Comparable](xs: list[T]) -> Iterator[Own[T]]:
    for x in xs:
        yield copy(x)

# TWO yields, so this body takes the RESUMABLE FRAME: the loop var is a `T*`
# frame field, and the open-T copy must carry the deref (`T((*x))`).
def each_twice[T: Comparable](xs: list[T]) -> Iterator[Own[T]]:
    for x in xs:
        yield copy(x)  # tpyc: ok
        yield copy(x)  # tpyc: ok

# A user sink typed Iterable[Own[T]] fed an Iterator[Own[T]] -- the same
# conformance path as list(), beyond the builtin.
def total(xs: Iterable[Own[int32]]) -> int32:
    s: int32 = 0
    for x in xs:
        s += x
    return s

def main() -> None:
    nums: list[int32] = [3, 1, 2]
    print(list(each(nums)))
    print(total(each(nums)))

    items: list[Item] = [Item(3, 30), Item(1, 10)]
    for it in list(each(items)):
        print(it.key, it.tag)

    # The frame path yields COPIES too -- mutating the source afterwards
    # leaves the collected values alone.
    src: list[Item] = [Item(5, 50)]
    kept = list(each_twice(src))
    src[0].tag = 999
    print("frame", [k.tag for k in kept], src[0].tag)
    print("frame_scalar", total(each_twice(nums)))

main()
