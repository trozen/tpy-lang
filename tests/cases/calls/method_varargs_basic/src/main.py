# *args on a user-record instance method: individual-arg packing and *list unpack.
from tpy import int32


class Calculator:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def sum_with_base(self, *xs: int32) -> int32:
        total: int32 = self.base
        for x in xs:
            total += x
        return total


def main() -> None:
    c = Calculator(int32(100))
    print(c.sum_with_base())
    print(c.sum_with_base(int32(1)))
    print(c.sum_with_base(int32(1), int32(2), int32(3)))
    nums = [int32(10), int32(20), int32(30)]
    print(c.sum_with_base(*nums))


main()
