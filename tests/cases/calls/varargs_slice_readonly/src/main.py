# Slice on a readonly vararg (`*args: readonly[T]`) -- exercises the
# const overload of `list_slice(const varargs<T, false>&, ...)` and verifies
# the slice result keeps the const element type.
from tpy import int32, readonly


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def sum_tail(*items: readonly[Box]) -> int32:  # tpyc: ok
    n: int32 = 0
    for b in items[1:]:
        n += b.val
    return n


def main() -> None:
    print(sum_tail(Box(1), Box(2), Box(3)))


main()
