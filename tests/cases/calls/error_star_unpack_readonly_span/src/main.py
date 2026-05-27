# Unpacking a genuine Span[readonly[T]] (non-value T) into a *args slot is
# rejected cleanly. The slot is mutable (varargs<T> exposes operator[] -> T&),
# so a readonly source would alias readonly data into a mutable vararg, and
# there is no const-span varargs constructor -- previously a silent C++-build
# miscompile. Mirrors the per-arg readonly gate the non-unpack path gets.
from tpy import Int32, Span, readonly, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def take_all(*items: Box) -> Int32:
    n: Int32 = 0
    for b in items:
        n += b.val
    return n


def use(xs: Span[readonly[Box]]) -> Int32:
    return take_all(*xs)  # tpyc: error(/Cannot pass readonly\[Box\] as mutable Box when unpacking into \*args/)


def main() -> None:
    items: list[Box] = []
    items.append(Box(1))
    print(use(items))


main()
