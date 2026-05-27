# Unpacking a Span[readonly[T]] into a readonly *args slot now works: the
# const-span source constructs varargs<const T> directly and the body cannot
# mutate. (Into a *mutable* vararg it stays rejected -- see
# error_star_unpack_readonly_span.) A mutable Span[T] unpacked into a readonly
# slot is also fine (span<T> -> span<const T>).
from tpy import Int32, Span, readonly, nocopy


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


def from_ro_span(xs: Span[readonly[Box]]) -> Int32:
    return take_ro(*xs)


def from_mut_span(xs: Span[Box]) -> Int32:
    return take_ro(*xs)


def main() -> None:
    items: list[Box] = []
    items.append(Box(3))
    items.append(Box(4))
    print(from_ro_span(items))
    print(from_mut_span(items))


main()
