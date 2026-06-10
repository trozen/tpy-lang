# An Iterator[Own[T]] argument binds an Iterable[Own[T]] param of an OVERLOADED
# callee (the resolve_overload multi-candidate path, distinct from the single-
# overload check in own_iter_to_iterable). The scalar overload is never called
# (CPython's plain int is not the Int32 stub); it only forces multi-candidate
# resolution. copy() makes owned copies, so copy semantics are intended here.
from tpy import Int32, Comparable, Own, copy
from typing import Iterator, Iterable, overload


class Item:
    key: Int32
    def __init__(self, key: Int32) -> None:
        self.key = key
    def __lt__(self, other: 'Item') -> bool:
        return self.key < other.key


def each[T: Comparable](xs: list[T]) -> Iterator[Own[T]]:
    for x in xs:
        yield copy(x)


@overload
def total(xs: Iterable[Own[Int32]]) -> Int32: ...
@overload
def total(xs: Int32) -> Int32: ...
def total(xs: Iterable[Own[Int32]] | Int32) -> Int32:
    if isinstance(xs, Int32):
        return xs
    s = 0
    for x in xs:  # tpyc: ok
        s += x
    return s


@overload
def keysum(xs: Iterable[Own[Item]]) -> Int32: ...
@overload
def keysum(xs: Int32) -> Int32: ...
def keysum(xs: Iterable[Own[Item]] | Int32) -> Int32:
    if isinstance(xs, Int32):
        return xs
    s = 0
    for it in xs:
        s += it.key
    return s


def main() -> None:
    nums: list[Int32] = [3, 1, 2]
    print(total(each(nums)))    # tpyc: ok

    items: list[Item] = [Item(3), Item(1), Item(2)]
    print(keysum(each(items)))  # tpyc: ok


main()
