# Forwarding *args into a readonly *args slot. A readonly source forwards via
# the varargs copy-ctor; a mutable vararg forwards via the const-view
# converting ctor (varargs<T> -> varargs<const T>).
from tpy import readonly, Int32, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def take_ro(*items: readonly[Box]) -> Int32:
    total: Int32 = 0
    for b in items:
        total += b.val
    return total


def forward_mutable(*xs: Box) -> Int32:
    return take_ro(*xs)


def forward_readonly(*xs: readonly[Box]) -> Int32:
    return take_ro(*xs)


def main() -> None:
    a = Box(5)
    b = Box(6)
    print(forward_mutable(a, b))
    print(forward_readonly(a, b))


main()
