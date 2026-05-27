# A *unpack whose element type does not match the vararg slot is rejected as a
# clean type error. The container is forwarded wholesale to varargs<T>, which
# cannot apply a per-element conversion -- previously this slipped past sema
# and failed deep in the C++ build with an opaque no-matching-constructor error.
from tpy import Int32, Span, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def take_all(*items: Box) -> Int32:
    n: Int32 = 0
    for b in items:
        n += 1
    return n


def use(xs: Span[Int32]) -> Int32:
    return take_all(*xs)  # tpyc: error(/Type mismatch in \*args \(unpacked element\): expected Box, got Int32/)


def main() -> None:
    nums: list[Int32] = []
    nums.append(1)
    print(use(nums))


main()
