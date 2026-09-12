# readonly *args: `*items: readonly[T]` is a genuinely-readonly vararg.
# The body gets const element access (cannot mutate); mutable args may be
# passed in (adding const is safe). Covers reference- and value-type elements.
from tpy import int32, readonly, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def sum_boxes(*items: readonly[Box]) -> int32:
    total: int32 = 0
    for b in items:
        total += b.val
    return total


def sum_ints(*nums: readonly[int32]) -> int32:
    total: int32 = 0
    for x in nums:
        total += x
    return total


def main() -> None:
    a = Box(10)
    b = Box(20)
    print(sum_boxes(a, b))
    print(sum_ints(1, 2, 3))


main()
