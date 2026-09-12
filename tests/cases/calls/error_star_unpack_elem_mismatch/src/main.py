# A *unpack whose element type does not match the vararg slot is rejected as a
# clean type error. The container is forwarded wholesale to varargs<T>, which
# cannot apply a per-element conversion -- previously this slipped past sema
# and failed deep in the C++ build with an opaque no-matching-constructor error.
from tpy import int32, Span, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def take_all(*items: Box) -> int32:
    n: int32 = 0
    for b in items:
        n += 1
    return n


def use(xs: Span[int32]) -> int32:
    return take_all(*xs)  # tpyc: error(/Type mismatch in \*args \(unpacked element\): expected Box, got int32/)


def main() -> None:
    nums: list[int32] = []
    nums.append(1)
    print(use(nums))


main()
