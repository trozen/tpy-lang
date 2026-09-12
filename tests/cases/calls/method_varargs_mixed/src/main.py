# Fixed positional + *args + keyword-only params on a method.
from tpy import int32


class Counter:
    label: int32

    def __init__(self, label: int32) -> None:
        self.label = label

    def total(self, base: int32, *values: int32, multiplier: int32 = int32(1)) -> int32:
        result: int32 = self.label + base
        for v in values:
            result += v
        return result * multiplier


def main() -> None:
    c = Counter(int32(10))
    print(c.total(int32(1)))
    print(c.total(int32(1), int32(2), int32(3)))
    print(c.total(int32(1), int32(2), int32(3), multiplier=int32(10)))


main()
