# Non-mutating *args: Box body infers the slot as readonly, so codegen emits
# `varargs<const Box>` and the caller's params can themselves be const-inferred
# (`Box& b, Box& c` collapses to `const Box& b, const Box& c`). Parallel to
# the existing non-vararg ref-param auto-const inference.
from tpy import Int32, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def sum_all(*items: Box) -> Int32:  # tpyc: ok
    n: Int32 = 0
    for b in items:
        n += b.val
    return n


def via_param(b: Box, c: Box) -> Int32:
    return sum_all(b, c)


def main() -> None:
    x = Box(3)
    y = Box(4)
    print(via_param(x, y))


main()
