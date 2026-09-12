# Integer literals coerce to fixed-width unsigned params at call sites.
# Happy path: direct literals, multi-arg, method receivers, all UInt widths.
from tpy import uint8, uint16, uint32, uint64


def take_u8(x: uint8) -> uint8:
    return x


def take_u16(x: uint16) -> uint16:
    return x


def take_u32(x: uint32) -> uint32:
    return x


def take_u64(x: uint64) -> uint64:
    return x


def take_pair(a: uint64, b: uint32) -> uint64:
    return a


class Counter:
    n: uint64

    def __init__(self) -> None:
        self.n = uint64(0)

    def bump(self, by: uint32) -> uint64:
        self.n = self.n + uint64(by)
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
