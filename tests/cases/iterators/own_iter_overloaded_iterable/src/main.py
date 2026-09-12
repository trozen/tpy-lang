# An Iterator[Own[T]] argument binds an Iterable[Own[T]] param of an OVERLOADED
# callee (the resolve_overload multi-candidate path, distinct from the single-
# overload check in own_iter_to_iterable). The scalar overload is never called
# (CPython's plain int is not the int32 stub); it only forces multi-candidate
# resolution. copy() makes owned copies, so copy semantics are intended here.
from tpy import int32, Comparable, Own, copy
from typing import Iterator, Iterable, overload


class Item:
    key: int32
    def __init__(self, key: int32) -> None:
        self.key = key
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


@overload
def total(xs: Iterable[Own[int32]]) -> int32: ...
@overload
def total(xs: int32) -> int32: ...
def total(xs: Iterable[Own[int32]] | int32) -> int32:
    if isinstance(xs, int32):
        return xs
    s = 0
    for x in xs:  # tpyc: ok
        s += x
    return s


@overload
def keysum(xs: Iterable[Own[Item]]) -> int32: ...
@overload
def keysum(xs: int32) -> int32: ...
def keysum(xs: Iterable[Own[Item]] | int32) -> int32:
    if isinstance(xs, int32):
        return xs
    s = 0
    for it in xs:
        s += it.key
    return s


def main() -> None:
    nums: list[int32] = [3, 1, 2]
    print(total(each(nums)))    # tpyc: ok

    items: list[Item] = [Item(3), Item(1), Item(2)]
    print(keysum(each(items)))  # tpyc: ok

    # Same overload resolution with the frame-shaped generator on the left.
    print(total(each_twice(nums)))     # tpyc: ok
    print(keysum(each_twice(items)))   # tpyc: ok


main()
