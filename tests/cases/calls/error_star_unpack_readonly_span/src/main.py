# Unpacking a genuine Span[readonly[T]] (non-value T) into a *args slot is
# rejected cleanly. The slot is mutable (varargs<T> exposes operator[] -> T&),
# so a readonly source would alias readonly data into a mutable vararg, and
# there is no const-span varargs constructor -- previously a silent C++-build
# miscompile. Mirrors the per-arg readonly gate the non-unpack path gets.
from tpy import int32, Span, readonly, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def take_all(*items: Box) -> int32:
    n: int32 = 0
    for b in items:
        n += b.val
    return n


def use(xs: Span[readonly[Box]]) -> int32:
    return take_all(*xs)  # tpyc: error(/Cannot pass readonly\[Box\] as mutable Box when unpacking into \*args/)


def main() -> None:
    items: list[Box] = []
    items.append(Box(1))
    print(use(items))


main()
