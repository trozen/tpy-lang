# Slice bounds: negative indices, out-of-range indices, empty slices.
# varargs::slice mirrors Python clamp semantics (negative index wraps via
# +size_; out-of-range clamps; i >= j returns empty).
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def sum_slice(start: int32, stop: int32, *items: Box) -> int32:
    n: int32 = 0
    for b in items[start:stop]:
        n += b.val
    return n


def main() -> None:
    # items = [Box(1), Box(2), Box(3), Box(4)]
    print(sum_slice(0, 4, Box(1), Box(2), Box(3), Box(4)))  # 10 -- full
    print(sum_slice(1, 3, Box(1), Box(2), Box(3), Box(4)))  # 5 -- [1:3]
    print(sum_slice(-2, 4, Box(1), Box(2), Box(3), Box(4)))  # 7 -- last two
    print(sum_slice(2, 100, Box(1), Box(2), Box(3), Box(4)))  # 7 -- clamp upper
    print(sum_slice(3, 1, Box(1), Box(2), Box(3), Box(4)))  # 0 -- empty (i>=j)
    print(sum_slice(-2, -1, Box(1), Box(2), Box(3), Box(4)))  # 3 -- both negative


main()
