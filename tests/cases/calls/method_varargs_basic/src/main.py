# *args on a user-record instance method: individual-arg packing and *list unpack.
from tpy import Int32


class Calculator:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def sum_with_base(self, *xs: Int32) -> Int32:
        total: Int32 = self.base
        for x in xs:
            total += x
        return total


def main() -> None:
    c = Calculator(Int32(100))
    print(c.sum_with_base())
    print(c.sum_with_base(Int32(1)))
    print(c.sum_with_base(Int32(1), Int32(2), Int32(3)))
    nums = [Int32(10), Int32(20), Int32(30)]
    print(c.sum_with_base(*nums))


main()
