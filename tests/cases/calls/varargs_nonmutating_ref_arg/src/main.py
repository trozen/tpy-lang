# A reference-type value passed as an individual arg into a *mutable* *args
# slot whose callee does NOT mutate. The slot address-takes each arg (&arg into
# a T* array for varargs<T>), so the source must stay a non-const lvalue even
# though it's never actually mutated -- previously this miscompiled with
# "invalid conversion from 'const Box*' to 'Box*'".
from tpy import Int32, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def sum_all(*items: Box) -> Int32:
    n: Int32 = 0
    for b in items:
        n += b.val
    return n


def via_param(b: Box, c: Box) -> Int32:
    return sum_all(b, c)  # tpyc: ok


def main() -> None:
    x = Box(3)
    y = Box(4)
    print(via_param(x, y))


main()
