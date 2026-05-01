# Integer literals coerce to fixed-width unsigned params at call sites.
# Happy path: direct literals, multi-arg, method receivers, all UInt widths.
from tpy import UInt8, UInt16, UInt32, UInt64


def take_u8(x: UInt8) -> UInt8:
    return x


def take_u16(x: UInt16) -> UInt16:
    return x


def take_u32(x: UInt32) -> UInt32:
    return x


def take_u64(x: UInt64) -> UInt64:
    return x


def take_pair(a: UInt64, b: UInt32) -> UInt64:
    return a


class Counter:
    n: UInt64

    def __init__(self) -> None:
        self.n = UInt64(0)

    def bump(self, by: UInt32) -> UInt64:
        self.n = self.n + UInt64(by)
        return self.n


def main() -> None:
    # zero literal at every fixed-unsigned width
    print(take_u8(0))
    print(take_u16(0))
    print(take_u32(0))
    print(take_u64(0))

    # positive literals at boundary values that fit
    print(take_u8(255))
    print(take_u16(65535))
    print(take_u32(4294967295))
    print(take_u64(255))

    # multi-arg
    print(take_pair(0, 0))
    print(take_pair(42, 7))

    # method receiver
    c = Counter()
    print(c.bump(1))
    print(c.bump(2))


main()
