# A movable BigInt local written into an int32 field: the narrowing coercion
# renders a fresh scalar prvalue, so the last-use move must not wrap it.
# Movability needs the tuple-unpack REBIND -- a plain unpack target binds
# `const BigInt&` and is never movable to begin with.
from tpy import int32


class Split:
    hi: int32
    lo: int32

    def __init__(self, total: int, extra: int) -> None:
        hi, lo = divmod(total, 60)
        lo = lo + extra
        carry, lo = divmod(lo, 60)
        hi = hi + carry
        if hi > 1000:
            raise OverflowError("too large")
        self.hi = hi
        self.lo = lo


def main():
    s = Split(3725, 10)
    print(s.hi)
    print(s.lo)


main()
