# Fixed positional + *args + keyword-only params on a method.
from tpy import Int32


class Counter:
    label: Int32

    def __init__(self, label: Int32) -> None:
        self.label = label

    def total(self, base: Int32, *values: Int32, multiplier: Int32 = Int32(1)) -> Int32:
        result: Int32 = self.label + base
        for v in values:
            result += v
        return result * multiplier


def main() -> None:
    c = Counter(Int32(10))
    print(c.total(Int32(1)))
    print(c.total(Int32(1), Int32(2), Int32(3)))
    print(c.total(Int32(1), Int32(2), Int32(3), multiplier=Int32(10)))


main()
