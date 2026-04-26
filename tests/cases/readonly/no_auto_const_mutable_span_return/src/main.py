# A method returning a mutable Span[T] from self.field must NOT be
# auto-const-inferred -- doing so would force the C++ return type
# from std::span<int32_t> to std::span<const int32_t>, silently
# making the declared mutable Span unusable for caller-side mutation.
# Guards the conservative branch in view_is_inherently_const.

from tpy import Int32, Span


class Buffer:
    _items: list[Int32]

    def __init__(self) -> None:
        self._items = [Int32(1), Int32(2), Int32(3)]

    def items(self) -> Span[Int32]:
        return self._items


def main() -> None:
    b = Buffer()
    s = b.items()
    s[Int32(0)] = Int32(99)
    print(b._items[0])


main()
