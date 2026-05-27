# Unpacking a reference-type container *parameter* (list[T], passed by C++
# reference -> Ref[list[T]]) into *args. Into a mutable slot the source is
# kept non-const (mutable borrow); into a readonly slot it borrows const.
from tpy import Int32, readonly, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def take_mut(*items: Box) -> Int32:
    n: Int32 = 0
    for b in items:
        n += b.val
    return n


def take_ro(*items: readonly[Box]) -> Int32:
    n: Int32 = 0
    for b in items:
        n += b.val
    return n


def via_mut(xs: list[Box]) -> Int32:
    return take_mut(*xs)


def via_ro(xs: list[Box]) -> Int32:
    return take_ro(*xs)


def main() -> None:
    items: list[Box] = []
    items.append(Box(10))
    items.append(Box(20))
    print(via_mut(items))
    print(via_ro(items))


main()
